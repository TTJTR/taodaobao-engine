from pathlib import Path

from app.ai.trust_evaluation import load_trust_claim_suite, trust_suite_coverage


def test_trust_claim_seed_suite_is_valid_and_covers_four_states() -> None:
    suite = load_trust_claim_suite(Path("evals/trust_claims_v1.json"))
    coverage = trust_suite_coverage(suite)

    assert len(suite.cases) == 30
    assert suite.schema_version == "solution-v2"
    assert all(count > 0 for count in coverage.values())
    assert any(case.type == "owner_assignment" for case in suite.cases)
    assert any(case.source_valid is False for case in suite.cases)
