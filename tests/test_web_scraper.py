import hashlib

import httpx
import pytest

from app.core.errors import AppError, ErrorCode
from app.integrations.web_scraper import WebScraperAdapter


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    del host, port
    return ("93.184.216.34",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.1.1",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
    ],
)
async def test_rejects_non_public_resolved_addresses(address: str) -> None:
    async def resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        return (address,)

    scraper = WebScraperAdapter(resolver=resolver, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(AppError) as caught:
        await scraper.fetch("https://example.com/news")

    assert caught.value.code == ErrorCode.UNSAFE_EXTERNAL_URL


@pytest.mark.asyncio
async def test_revalidates_redirect_destination() -> None:
    requested: list[str] = []

    async def resolver(host: str, port: int) -> tuple[str, ...]:
        del port
        return ("192.168.1.10",) if host == "internal.example" else ("93.184.216.34",)

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})

    scraper = WebScraperAdapter(resolver=resolver, transport=httpx.MockTransport(handler))

    with pytest.raises(AppError) as caught:
        await scraper.fetch("https://example.com/start")

    assert caught.value.code == ErrorCode.UNSAFE_EXTERNAL_URL
    assert requested == ["https://example.com/start"]


@pytest.mark.asyncio
async def test_rejects_private_address_among_multiple_dns_results() -> None:
    async def resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        return ("93.184.216.34", "10.0.0.8")

    scraper = WebScraperAdapter(resolver=resolver)

    with pytest.raises(AppError) as caught:
        await scraper.fetch("https://example.com/news")

    assert caught.value.code == ErrorCode.UNSAFE_EXTERNAL_URL


@pytest.mark.asyncio
async def test_rejects_private_connected_peer_after_public_dns_result() -> None:
    class NetworkStream:
        def get_extra_info(self, name: str):
            assert name == "server_addr"
            return ("127.0.0.1", 443)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not reached",
            extensions={"network_stream": NetworkStream()},
        )

    scraper = WebScraperAdapter(
        resolver=public_resolver,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AppError) as caught:
        await scraper.fetch("https://example.com/news")

    assert caught.value.code == ErrorCode.UNSAFE_EXTERNAL_URL


@pytest.mark.asyncio
async def test_extracts_article_and_returns_immutable_artifact() -> None:
    html = (
        b"<html><body><article><h1>Tender notice</h1>"
        b"<p>Important procurement details.</p></article></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html,
        )

    artifact = await WebScraperAdapter(
        resolver=public_resolver,
        transport=httpx.MockTransport(handler),
    ).fetch("https://example.com/tender")

    assert artifact.status == "captured"
    assert "Important procurement details" in artifact.text_content
    assert artifact.content_sha256 == hashlib.sha256(html).hexdigest()
    assert artifact.byte_size == len(html)
    assert artifact.as_raw_artifact()["provider"] == "web_scraper"
    with pytest.raises((AttributeError, TypeError)):
        artifact.status = "partial"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_falls_back_to_truncated_html_when_extraction_fails(monkeypatch) -> None:
    monkeypatch.setattr("app.integrations.web_scraper.trafilatura.extract", lambda _: None)
    html = b"<html><body>raw fallback body</body></html>"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=html))

    artifact = await WebScraperAdapter(
        resolver=public_resolver,
        transport=transport,
        fallback_html_chars=20,
    ).fetch("https://example.com/empty")

    assert artifact.status == "partial"
    assert artifact.text_content == html.decode()[:20]
    assert artifact.extraction_succeeded is False


@pytest.mark.asyncio
async def test_rejects_oversized_streamed_response() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 11))
    scraper = WebScraperAdapter(
        resolver=public_resolver,
        transport=transport,
        max_response_bytes=10,
    )

    with pytest.raises(AppError) as caught:
        await scraper.fetch("https://example.com/large")

    assert caught.value.code == ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "http://user:password@example.com", "https:///missing-host"],
)
async def test_rejects_unsafe_url_shapes(url: str) -> None:
    scraper = WebScraperAdapter(resolver=public_resolver)

    with pytest.raises(AppError) as caught:
        await scraper.fetch(url)

    assert caught.value.code == ErrorCode.UNSAFE_EXTERNAL_URL
