import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from urllib.parse import urljoin

import httpx
import trafilatura

from app.core.errors import AppError, ErrorCode

Resolver = Callable[[str, int], Awaitable[tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class WebScrapeArtifact:
    source_url: str
    normalized_url: str
    status: Literal["captured", "partial"]
    mime_type: str | None
    http_status: int
    content_sha256: str
    byte_size: int
    text_content: str
    captured_at: datetime
    response_time_ms: int
    redirect_count: int
    resolved_ips: tuple[str, ...]
    extraction_succeeded: bool

    def as_raw_artifact(self) -> dict[str, object]:
        """Return service-layer fields without assigning persistence identity."""
        return {
            "kind": "web_page",
            "status": self.status,
            "provider": "web_scraper",
            "source_url": self.source_url,
            "normalized_url": self.normalized_url,
            "mime_type": self.mime_type,
            "http_status": self.http_status,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "text_content": self.text_content,
            "captured_at": self.captured_at,
            "security_report": {
                "ssrf_validated": True,
                "resolved_ips": list(self.resolved_ips),
                "redirect_count": self.redirect_count,
            },
            "metadata_snapshot": {
                "response_time_ms": self.response_time_ms,
                "extraction_succeeded": self.extraction_succeeded,
            },
        }


class WebScraperAdapter:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 5 * 1024 * 1024,
        fallback_html_chars: int = 200_000,
        max_redirects: int = 5,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.fallback_html_chars = fallback_html_chars
        self.max_redirects = max_redirects
        self._resolver = resolver or _resolve_host
        self._transport = transport

    async def fetch(self, url: str) -> WebScrapeArtifact:
        source_url = url
        current_url = _validated_url(url)
        started = perf_counter()
        redirects = 0
        all_resolved_ips: list[str] = []
        headers = {
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "User-Agent": "XianjintuanIntelligenceBot/2.0",
        }
        async with httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=self.timeout_seconds,
            transport=self._transport,
            headers=headers,
        ) as client:
            while True:
                resolved_ips = await self._validate_destination(current_url)
                all_resolved_ips.extend(
                    address for address in resolved_ips if address not in all_resolved_ips
                )
                try:
                    async with client.stream("GET", current_url) as response:
                        self._validate_peer_address(response)
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise _unavailable("Redirect response has no Location header")
                            if redirects >= self.max_redirects:
                                raise _unavailable("Maximum redirect count exceeded")
                            current_url = _validated_url(urljoin(str(current_url), location))
                            redirects += 1
                            continue
                        if response.status_code < 200 or response.status_code >= 300:
                            raise _unavailable(
                                f"Upstream returned HTTP {response.status_code}",
                                details={"http_status": response.status_code},
                            )
                        body = await self._read_limited(response)
                        mime_type = (
                            response.headers.get("content-type", "").split(";", 1)[0] or None
                        )
                        status_code = response.status_code
                except AppError:
                    raise
                except httpx.HTTPError as exc:
                    raise _unavailable("Web resource request failed", retryable=True) from exc

                html = body.decode(response.encoding or "utf-8", errors="replace")
                extracted = trafilatura.extract(html)
                extraction_succeeded = bool(extracted and extracted.strip())
                text_content = (
                    extracted.strip()
                    if extraction_succeeded and extracted is not None
                    else html[: self.fallback_html_chars]
                )
                return WebScrapeArtifact(
                    source_url=source_url,
                    normalized_url=str(current_url),
                    status="captured" if extraction_succeeded else "partial",
                    mime_type=mime_type,
                    http_status=status_code,
                    content_sha256=hashlib.sha256(body).hexdigest(),
                    byte_size=len(body),
                    text_content=text_content,
                    captured_at=datetime.now(UTC),
                    response_time_ms=max(0, round((perf_counter() - started) * 1000)),
                    redirect_count=redirects,
                    resolved_ips=tuple(all_resolved_ips),
                    extraction_succeeded=extraction_succeeded,
                )

    async def _validate_destination(self, url: httpx.URL) -> tuple[str, ...]:
        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            addresses = await self._resolver(url.host, port)
        except (OSError, UnicodeError) as exc:
            raise _unavailable("Target host could not be resolved", retryable=True) from exc
        if not addresses:
            raise _unavailable("Target host resolved to no addresses", retryable=True)
        for address in addresses:
            _validate_public_ip(address)
        return tuple(dict.fromkeys(addresses))

    async def _read_limited(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_response_bytes:
                    raise _unavailable("Response body exceeds configured size limit")
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise _unavailable("Response body exceeds configured size limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _validate_peer_address(response: httpx.Response) -> None:
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            return
        peer = stream.get_extra_info("server_addr")
        if isinstance(peer, tuple) and peer:
            _validate_public_ip(str(peer[0]))


async def _resolve_host(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


def _validated_url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
    except (TypeError, httpx.InvalidURL) as exc:
        raise _unsafe("Malformed target URL") from exc
    if url.scheme not in {"http", "https"}:
        raise _unsafe("Only HTTP and HTTPS URLs are allowed")
    if not url.host:
        raise _unsafe("Target URL must include a host")
    if url.userinfo:
        raise _unsafe("Credentials in target URLs are not allowed")
    return url


def _validate_public_ip(value: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise _unsafe("Resolver returned an invalid IP address") from exc
    if not address.is_global:
        raise _unsafe("Target resolves to a non-public IP address", details={"address": value})


def _unsafe(message: str, *, details: dict[str, object] | None = None) -> AppError:
    return AppError(
        ErrorCode.UNSAFE_EXTERNAL_URL,
        message,
        status_code=400,
        retryable=False,
        details=details,
    )


def _unavailable(
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, object] | None = None,
) -> AppError:
    return AppError(
        ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE,
        message,
        status_code=502,
        retryable=retryable,
        details=details,
    )
