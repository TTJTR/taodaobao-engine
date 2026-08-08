from pathlib import Path

from app.ai.schemas import RetrievalSnapshot, SolutionContext

EXAMPLE_DIRECTORY = Path(__file__).parents[2] / "examples" / "golden_a3"


def test_golden_context_matches_solution_context_schema() -> None:
    context = SolutionContext.model_validate_json(
        (EXAMPLE_DIRECTORY / "context.json").read_text(encoding="utf-8")
    )

    assert context.customer_profile.customer_name == "星瀚精工集团"
    assert "三个月内" in context.current_requirement


def test_golden_snapshot_contains_expected_verified_assets() -> None:
    snapshot = RetrievalSnapshot.model_validate_json(
        (EXAMPLE_DIRECTORY / "retrieval_snapshot.json").read_text(encoding="utf-8")
    )

    assert [item.asset_id for item in snapshot.experiences] == ["EXP-001", "EXP-002"]
    assert [item.asset_id for item in snapshot.capabilities] == [
        "CAP-016",
        "CAP-001",
        "CAP-002",
        "CAP-003",
        "CAP-013",
    ]
    assert snapshot.can_generate_solution is True
