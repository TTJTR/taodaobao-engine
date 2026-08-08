import asyncio

import pytest

from app.ai.embedding import (
    AssetType,
    BailianEmbeddingProvider,
    BailianEmbeddingSettings,
    EmbeddingInputError,
    MockEmbeddingProvider,
    content_fingerprint,
    cosine_similarity,
)


def test_mock_single_and_batch_embeddings_have_one_dimension_and_version() -> None:
    provider = MockEmbeddingProvider(dimension=32)

    single = asyncio.run(provider.embed("视觉质检", AssetType.QUERY))
    batch = asyncio.run(
        provider.embed_batch(
            ["视觉质检", "报表自动化"],
            AssetType.CAPABILITY,
        )
    )

    assert single.dimension == 32
    assert all(item.dimension == single.dimension for item in batch)
    assert all(item.embedding_version == provider.embedding_version for item in batch)
    assert single.asset_type == "query"
    assert all(item.asset_type == "capability" for item in batch)


def test_embedding_fingerprint_is_deterministic_after_whitespace_normalization() -> None:
    provider = MockEmbeddingProvider()

    first = asyncio.run(provider.embed("  视觉\n质检  ", AssetType.EXPERIENCE))
    second = asyncio.run(provider.embed("视觉 质检", AssetType.EXPERIENCE))

    assert first.content_fingerprint == second.content_fingerprint
    assert first.vector == second.vector
    assert first.content_fingerprint == content_fingerprint("视觉 质检")


@pytest.mark.parametrize("text", ["", "  ", "\n\t"])
def test_embedding_rejects_empty_text(text: str) -> None:
    with pytest.raises(EmbeddingInputError, match="must not be empty"):
        asyncio.run(MockEmbeddingProvider().embed(text, AssetType.QUERY))


def test_embedding_rejects_overlong_text_explicitly() -> None:
    provider = MockEmbeddingProvider(max_text_characters=5)

    with pytest.raises(EmbeddingInputError, match="exceeds"):
        asyncio.run(provider.embed("超过五个字符的文本", AssetType.QUERY))


def test_chinese_short_text_regression_prefers_related_asset() -> None:
    provider = MockEmbeddingProvider(dimension=128)
    query = asyncio.run(provider.embed("视觉质检人工复看", AssetType.QUERY))
    related = asyncio.run(provider.embed("工业视觉质检降低人工图片复看", AssetType.EXPERIENCE))
    unrelated = asyncio.run(provider.embed("财务月度报表自动汇总", AssetType.EXPERIENCE))

    assert cosine_similarity(query.vector, related.vector) > cosine_similarity(
        query.vector,
        unrelated.vector,
    )


def test_bailian_response_is_reordered_by_index_and_checked_for_dimension() -> None:
    provider = BailianEmbeddingProvider(BailianEmbeddingSettings(api_key="test-only", dimension=2))
    response = {
        "data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]
    }

    vectors = provider._parse_vectors(response, expected_count=2)

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
