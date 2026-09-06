import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai.runtime import ACTIVE_TRUST_BUDGET

BAILIAN_BEIJING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class ModelClientError(RuntimeError):
    """Raised when the model API cannot return a valid response."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        code: str = "model_error",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.code = code


def load_env_file(path: Path) -> None:
    """Load missing environment variables without overwriting shell values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class BailianSettings:
    api_key: str
    chat_model: str = "qwen-plus"
    base_url: str = BAILIAN_BEIJING_BASE_URL
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    temperature: float = 0.1

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "BailianSettings":
        if env_file is not None:
            load_env_file(env_file)
        api_key = (
            os.getenv("AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
        ).strip()
        if not api_key:
            raise ModelClientError("AI_API_KEY is not configured")
        return cls(
            api_key=api_key,
            chat_model=os.getenv("CHAT_MODEL", "qwen-plus").strip() or "qwen-plus",
            base_url=(
                os.getenv("AI_BASE_URL")
                or os.getenv("BAILIAN_BASE_URL")
                or BAILIAN_BEIJING_BASE_URL
            ).rstrip("/"),
            timeout_seconds=float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "60")),
            max_retries=max(0, int(os.getenv("BAILIAN_MAX_RETRIES", "2"))),
            retry_backoff_seconds=max(
                0.0,
                float(os.getenv("BAILIAN_RETRY_BACKOFF_SECONDS", "0.5")),
            ),
            temperature=float(os.getenv("BAILIAN_TEMPERATURE", "0.1")),
        )


class BailianChatClient:
    def __init__(self, settings: BailianSettings) -> None:
        self._settings = settings
        self._last_attempt_count: ContextVar[int] = ContextVar(
            f"bailian_attempt_count_{id(self)}",
            default=0,
        )

    @property
    def last_attempt_count(self) -> int:
        return self._last_attempt_count.get()

    @property
    def model_version(self) -> str:
        return self._settings.chat_model

    @property
    def parameter_version(self) -> str:
        return f"temperature={self._settings.temperature};json_object=true"

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        result, attempt_count = await asyncio.to_thread(
            self._generate_json_sync_with_attempts,
            system_prompt,
            user_prompt,
        )
        self._last_attempt_count.set(attempt_count)
        return result

    def _generate_json_sync(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        result, attempt_count = self._generate_json_sync_with_attempts(
            system_prompt,
            user_prompt,
        )
        self._last_attempt_count.set(attempt_count)
        return result

    def _generate_json_sync_with_attempts(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any], int]:
        payload = {
            "model": self._settings.chat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._settings.temperature,
            "response_format": {"type": "json_object"},
        }
        last_error: ModelClientError | None = None
        for attempt in range(1, self._settings.max_retries + 2):
            try:
                budget = ACTIVE_TRUST_BUDGET.get()
                if budget is not None:
                    budget.consume_attempt()
                response_data = self._request_once(payload)
                return self._read_message_json(response_data), attempt
            except ModelClientError as exc:
                last_error = exc
                if not exc.retryable or attempt > self._settings.max_retries:
                    raise
                time.sleep(self._settings.retry_backoff_seconds * attempt)
        if last_error is not None:  # pragma: no cover - loop always raises or returns
            raise last_error
        raise ModelClientError("Bailian request ended unexpectedly")  # pragma: no cover

    def _request_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._settings.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._settings.timeout_seconds,
            ) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {408, 409, 429, 500, 502, 503, 504}
            raise ModelClientError(
                f"Bailian returned HTTP {exc.code}",
                retryable=retryable,
                status_code=exc.code,
                code="http_error",
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", str(exc))
            raise ModelClientError(
                f"Could not connect to Bailian: {reason}",
                retryable=True,
                code="connection_error",
            ) from exc
        except json.JSONDecodeError as exc:
            raise ModelClientError(
                "Bailian returned a non-JSON API response",
                code="invalid_api_response",
            ) from exc
        if not isinstance(response_data, dict):
            raise ModelClientError(
                "Bailian API response must be an object",
                code="invalid_api_response",
            )
        return response_data

    def _read_message_json(self, response_data: dict[str, Any]) -> dict[str, Any]:
        try:
            content = response_data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(
                "Bailian response is missing message content",
                code="missing_message_content",
            ) from exc
        return self._parse_json_content(content)

    @staticmethod
    def _parse_json_content(content: Any) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ModelClientError("Model message content is not text", code="invalid_model_json")
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelClientError(
                "Model did not return valid JSON",
                code="invalid_model_json",
            ) from exc
        if not isinstance(parsed, dict):
            raise ModelClientError("Model JSON output must be an object", code="invalid_model_json")
        return parsed
