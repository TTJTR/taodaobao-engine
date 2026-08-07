from datetime import UTC, datetime
from pathlib import Path

from app.ai.evaluation import (
    evaluate_solution,
    load_evaluation_suite,
    summarize_outcomes,
)

SUITE_PATH = Path("evals/business_cases.json")


def refusal_solution() -> dict:
    return {
        "requirement_understanding": [
            {
                "text": "当前知识库无足够依据，不能按当前要求生成正式方案。",
                "boundary": "ai_inference",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "initial_recommendations": [],
        "historical_evidence": [],
        "capability_composition": [],
        "prerequisites_and_risks": [],
        "pending_confirmations": [
            {
                "text": "需要补充可支持该需求的企业资料。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "sources": [],
        "suggested_questions": ["是否有相关产品或案例资料？"],
    }


def test_business_suite_contains_at_least_twenty_cases_and_all_categories() -> None:
    suite, cases = load_evaluation_suite(SUITE_PATH)

    assert suite.suite_version == "business-regression-v1"
    assert len(cases) >= 20
    assert {case.spec.category.value for case in cases} == {
        "normal",
        "boundary",
        "conflict",
        "no_evidence",
        "overreach",
    }


def test_no_evidence_case_scores_explicit_refusal() -> None:
    _, cases = load_evaluation_suite(SUITE_PATH)
    case = next(case for case in cases if case.spec.case_id == "E01")

    outcome = evaluate_solution(case, refusal_solution(), duration_ms=12.5)

    assert outcome.schema_passed is True
    assert outcome.no_basis_refused is True
    assert outcome.citations_supported is True
    assert outcome.boundary_correct is True
    assert outcome.passed is True


def test_evaluation_rejects_schema_failure_without_stopping_report() -> None:
    _, cases = load_evaluation_suite(SUITE_PATH)
    case = cases[0]

    outcome = evaluate_solution(case, {"not": "a solution"}, duration_ms=2.0)
    summary = summarize_outcomes([outcome])

    assert outcome.schema_passed is False
    assert outcome.error is not None
    assert summary.total_cases == 1
    assert summary.passed_cases == 0
    assert summary.schema_pass_rate == 0


def test_suite_snapshot_keeps_created_at_as_valid_datetime() -> None:
    _, cases = load_evaluation_suite(SUITE_PATH)

    assert isinstance(cases[0].retrieval_snapshot.created_at, datetime)
    assert cases[0].retrieval_snapshot.created_at.tzinfo is not None
    assert datetime.now(UTC).tzinfo is not None
