import asyncio
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from app.ai.model_client import BAILIAN_BEIJING_BASE_URL, ModelClientError, load_env_file
from app.ai.schemas.base import AISchema, NonEmptyStr

DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_MAX_TEXT_CHARACTERS = 12_000


class AssetType(StrEnum):
    EXPERIENCE = "experience"
    CAPABILITY = "capability"
    QUERY = "query"


class EmbeddingInputError(ValueError):
    """The input cannot be meaningfully embedded."""


class EmbeddingResult(AISchema):
    vector: list[float] = Field(min_length=1)
    dimension: int = Field(gt=0)
    embedding_version: NonEmptyStr
    content_fingerprint: NonEmptyStr
    asset_type: AssetType

    @model_validator(mode="after")
    def dimension_must_match_vector(self) -> "EmbeddingResult":
        if len(self.vector) != self.dimension:
            raise ValueError("dimension must match vector length")
        return self


class EmbeddingProvider(Protocol):
    @property
    def embedding_version(self) -> str: ...

    async def embed(self, text: str, asset_type: AssetType) -> EmbeddingResult: ...

    async def embed_batch(
        self,
        texts: list[str],
        asset_type: AssetType,
    ) -> list[EmbeddingResult]: ...


def normalize_embedding_text(text: str, max_characters: int) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        raise EmbeddingInputError("embedding text must not be empty")
    if len(normalized) > max_characters:
        raise EmbeddingInputError(f"embedding text exceeds the {max_characters} character limit")
    return normalized


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("vectors must be non-empty and have the same dimension")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


@dataclass(frozen=True)
class BailianEmbeddingSettings:
    api_key: str
    model: str = DEFAULT_EMBEDDING_MODEL
    dimension: int = DEFAULT_EMBEDDING_DIMENSION
    base_url: str = BAILIAN_BEIJING_BASE_URL
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    max_text_characters: int = DEFAULT_MAX_TEXT_CHARACTERS
    batch_size: int = 10

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "BailianEmbeddingSettings":
        if env_file is not None:
            load_env_file(env_file)
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            raise ModelClientError("DASHSCOPE_API_KEY is not configured")
        return cls(
            api_key=api_key,
            model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
            or DEFAULT_EMBEDDING_MODEL,
            dimension=int(os.getenv("EMBEDDING_DIMENSION", str(DEFAULT_EMBEDDING_DIMENSION))),
            base_url=os.getenv("BAILIAN_BASE_URL", BAILIAN_BEIJING_BASE_URL).rstrip("/"),
            timeout_seconds=float(os.getenv("BAILIAN_TIMEOUT_SECONDS", "60")),
            max_retries=max(0, int(os.getenv("BAILIAN_MAX_RETRIES", "2"))),
            retry_backoff_seconds=max(
                0.0,
                float(os.getenv("BAILIAN_RETRY_BACKOFF_SECONDS", "0.5")),
            ),
            max_text_characters=int(
                os.getenv(
                    "EMBEDDING_MAX_TEXT_CHARACTERS",
                    str(DEFAULT_MAX_TEXT_CHARACTERS),
                )
            ),
            batch_size=max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "10"))),
        )


class BailianEmbeddingProvider:
    def __init__(self, settings: BailianEmbeddingSettings) -> None:
        self._settings = settings

    @property
    def embedding_version(self) -> str:
        return f"{self._settings.model}:{self._settings.dimension}:v1"

    async def embed(self, text: str, asset_type: AssetType) -> EmbeddingResult:
        results = await self.embed_batch([text], asset_type)
        return results[0]

    async def embed_batch(
        self,
        texts: list[str],
        asset_type: AssetType,
    ) -> list[EmbeddingResult]:
        normalized = self._normalize_batch(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(normalized), self._settings.batch_size):
            chunk = normalized[start : start + self._settings.batch_size]
            chunk_vectors = await asyncio.to_thread(self._embed_chunk_sync, chunk)
            vectors.extend(chunk_vectors)
        return self._build_results(normalized, vectors, asset_type)

    def _normalize_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            raise EmbeddingInputError("embedding batch must not be empty")
        return [
            normalize_embedding_text(text, self._settings.max_text_characters) for text in texts
        ]

    def _embed_chunk_sync(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self._settings.model,
            "input": texts,
            "dimensions": self._settings.dimension,
            "encoding_format": "float",
        }
        last_error: ModelClientError | None = None
        for attempt in range(1, self._settings.max_retries + 2):
            try:
                return self._request_once(payload, len(texts))
            except ModelClientError as exc:
                last_error = exc
                if not exc.retryable or attempt > self._settings.max_retries:
                    raise
                time.sleep(self._settings.retry_backoff_seconds * attempt)
        if last_error is not None:  # pragma: no cover
            raise last_error
        raise ModelClientError("Embedding request ended unexpectedly")  # pragma: no cover

    def _request_once(
        self,
        payload: dict[str, object],
        expected_count: int,
    ) -> list[list[float]]:
        request = urllib.request.Request(
            f"{self._settings.base_url}/embeddings",
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
                f"Bailian embedding returned HTTP {exc.code}",
                retryable=retryable,
                status_code=exc.code,
                code="embedding_http_error",
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", str(exc))
            raise ModelClientError(
                f"Could not connect to Bailian embedding: {reason}",
                retryable=True,
                code="embedding_connection_error",
            ) from exc
        except json.JSONDecodeError as exc:
            raise ModelClientError(
                "Bailian embedding returned a non-JSON response",
                code="invalid_embedding_response",
            ) from exc
        return self._parse_vectors(response_data, expected_count)

    def _parse_vectors(self, response_data: object, expected_count: int) -> list[list[float]]:
        if not isinstance(response_data, dict) or not isinstance(response_data.get("data"), list):
            raise ModelClientError(
                "Bailian embedding response is missing data",
                code="invalid_embedding_response",
            )
        data = response_data["data"]
        try:
            ordered = sorted(data, key=lambda item: item["index"])
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError) as exc:
            raise ModelClientError(
                "Bailian embedding response contains invalid items",
                code="invalid_embedding_response",
            ) from exc
        if len(vectors) != expected_count:
            raise ModelClientError(
                "Bailian embedding response count does not match input",
                code="invalid_embedding_response",
            )
        if any(
            not isinstance(vector, list)
            or len(vector) != self._settings.dimension
            or not all(isinstance(value, int | float) for value in vector)
            for vector in vectors
        ):
            raise ModelClientError(
                "Bailian embedding response has an unexpected dimension",
                code="embedding_dimension_mismatch",
            )
        return [[float(value) for value in vector] for vector in vectors]

    def _build_results(
        self,
        texts: list[str],
        vectors: list[list[float]],
        asset_type: AssetType,
    ) -> list[EmbeddingResult]:
        if len(texts) != len(vectors):
            raise ModelClientError(
                "Embedding result count does not match input",
                code="invalid_embedding_response",
            )
        return [
            EmbeddingResult(
                vector=vector,
                dimension=self._settings.dimension,
                embedding_version=self.embedding_version,
                content_fingerprint=content_fingerprint(text),
                asset_type=asset_type,
            )
            for text, vector in zip(texts, vectors, strict=True)
        ]


class MockEmbeddingProvider:
    """Offline deterministic vectors for local development and contract tests."""

    def __init__(
        self,
        dimension: int = 64,
        max_text_characters: int = DEFAULT_MAX_TEXT_CHARACTERS,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension
        self._max_text_characters = max_text_characters

    @property
    def embedding_version(self) -> str:
        return f"mock-hash:{self._dimension}:v1"

    async def embed(self, text: str, asset_type: AssetType) -> EmbeddingResult:
        return (await self.embed_batch([text], asset_type))[0]

    async def embed_batch(
        self,
        texts: list[str],
        asset_type: AssetType,
    ) -> list[EmbeddingResult]:
        if not texts:
            raise EmbeddingInputError("embedding batch must not be empty")
        normalized = [normalize_embedding_text(text, self._max_text_characters) for text in texts]
        return [
            EmbeddingResult(
                vector=self._vectorize(text),
                dimension=self._dimension,
                embedding_version=self.embedding_version,
                content_fingerprint=content_fingerprint(text),
                asset_type=asset_type,
            )
            for text in normalized
        ]

    def _vectorize(self, text: str) -> list[float]:
        compact = text.casefold().replace(" ", "")
        tokens = list(compact)
        tokens.extend(compact[index : index + 2] for index in range(len(compact) - 1))
        vector = [0.0] * self._dimension
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
