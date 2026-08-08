import json
from copy import deepcopy
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from pydantic import Field

from app.ai.schemas import EvidenceBoundary, RetrievalSnapshot, Solution, SolutionContext
from app.ai.schemas.base import AISchema, NonEmptyStr


class EvaluationCategory(StrEnum):
    NORMAL = "normal"
    BOUNDARY = "boundary"
    CONFLICT = "conflict"
    NO_EVIDENCE = "no_evidence"
    OVERREACH = "overreach"


class EvaluationCaseSpec(AISchema):
    case_id: NonEmptyStr
    title: NonEmptyStr
    category: EvaluationCategory
    current_requirement: NonEmptyStr
    expected_asset_ids: list[NonEmptyStr] = Field(default_factory=list)
    expected_refusal: bool = False
    forbidden_claims: list[NonEmptyStr] = Field(default_factory=list)
    clear_experiences: bool = False
    clear_capabilities: bool = False
    can_generate_solution: bool | None = None
    manual_edit_count: int | None = Field(default=None, ge=0)


class EvaluationSuiteSpec(AISchema):
    suite_version: NonEmptyStr
    base_context_path: NonEmptyStr
    base_snapshot_path: NonEmptyStr
    cases: list[EvaluationCaseSpec] = Field(min_length=20)


class ResolvedEvaluationCase(AISchema):
    spec: EvaluationCaseSpec
    context: SolutionContext
    retrieval_snapshot: RetrievalSnapshot


class EvaluationOutcome(AISchema):
    case_id: NonEmptyStr
    category: EvaluationCategory
    schema_passed: bool
    expected_asset_hit: bool | None = None
    citations_supported: bool
    no_basis_refused: bool | None = None
    boundary_correct: bool
    forbidden_claims_absent: bool
    manual_edit_count: int | None = Field(default=None, ge=0)
    duration_ms: float = Field(ge=0)
    error: str | None = None

    @property
    def passed(self) -> bool:
        required = [
            self.schema_passed,
            self.citations_supported,
            self.boundary_correct,
            self.forbidden_claims_absent,
        ]
        if self.expected_asset_hit is not None:
            required.append(self.expected_asset_hit)
        if self.no_basis_refused is not None:
            required.append(self.no_basis_refused)
        return all(required)


class EvaluationSummary(AISchema):
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    schema_pass_rate: float = Field(ge=0, le=1)
    asset_hit_rate: float | None = Field(default=None, ge=0, le=1)
    citation_support_rate: float = Field(ge=0, le=1)
    refusal_pass_rate: float | None = Field(default=None, ge=0, le=1)
    boundary_pass_rate: float = Field(ge=0, le=1)
    average_duration_ms: float = Field(ge=0)
    manual_edit_count_total: int | None = Field(default=None, ge=0)


class EvaluationReport(AISchema):
    suite_version: NonEmptyStr
    run_at: datetime
    model_version: NonEmptyStr
    prompt_version: NonEmptyStr
    outcomes: list[EvaluationOutcome]
    summary: EvaluationSummary


class SolutionEngine(Protocol):
    async def generate_solution(
        self,
        context: dict[str, Any],
        retrieval_snapshot: dict[str, Any],
    ) -> dict[str, Any]: ...


def load_evaluation_suite(path: Path) -> tuple[EvaluationSuiteSpec, list[ResolvedEvaluationCase]]:
    raw_suite = json.loads(path.read_text(encoding="utf-8"))
    suite = EvaluationSuiteSpec.model_validate(raw_suite)
    project_root = path.parent.parent
    base_context = json.loads((project_root / suite.base_context_path).read_text(encoding="utf-8"))
    base_snapshot = json.loads(
        (project_root / suite.base_snapshot_path).read_text(encoding="utf-8")
    )

    resolved: list[ResolvedEvaluationCase] = []
    for spec in suite.cases:
        context = deepcopy(base_context)
        snapshot = deepcopy(base_snapshot)
        context["current_requirement"] = spec.current_requirement
        if spec.clear_experiences:
            snapshot["experiences"] = []
        if spec.clear_capabilities:
            snapshot["capabilities"] = []
        if spec.can_generate_solution is not None:
            snapshot["can_generate_solution"] = spec.can_generate_solution
        if spec.expected_refusal:
            snapshot["can_generate_solution"] = False
        resolved.append(
            ResolvedEvaluationCase(
                spec=spec,
                context=SolutionContext.model_validate(context),
                retrieval_snapshot=RetrievalSnapshot.model_validate(snapshot),
            )
        )
    return suite, resolved


def solution_text(solution: Solution) -> str:
    sections = (
        solution.requirement_understanding,
        solution.initial_recommendations,
        solution.historical_evidence,
        solution.capability_composition,
        solution.prerequisites_and_risks,
        solution.pending_confirmations,
    )
    return "\n".join(item.text for section in sections for item in section)


def cited_asset_ids(solution: Solution) -> set[str]:
    return {
        item.asset_id
        for section in (
            solution.historical_evidence,
            solution.capability_composition,
            solution.prerequisites_and_risks,
        )
        for item in section
        if item.asset_id is not None
    }


def citations_are_supported(
    solution: Solution,
    snapshot: RetrievalSnapshot,
) -> bool:
    allowed = {
        (item.asset_id, item.source_id): EvidenceBoundary.HISTORICAL_FACT
        for item in snapshot.experiences
    }
    allowed.update(
        {
            (item.asset_id, item.source_id): EvidenceBoundary.ENTERPRISE_CAPABILITY
            for item in snapshot.capabilities
        }
    )
    return all(
        allowed.get((item.asset_id, item.source_id)) == item.boundary
        for section in (
            solution.historical_evidence,
            solution.capability_composition,
            solution.prerequisites_and_risks,
        )
        for item in section
        if item.boundary
        in {EvidenceBoundary.HISTORICAL_FACT, EvidenceBoundary.ENTERPRISE_CAPABILITY}
    )


def boundaries_are_correct(solution: Solution) -> bool:
    return (
        all(
            item.boundary in {EvidenceBoundary.AI_INFERENCE, EvidenceBoundary.PENDING_CONFIRMATION}
            for item in solution.requirement_understanding
        )
        and all(
            item.boundary == EvidenceBoundary.AI_INFERENCE
            for item in solution.initial_recommendations
        )
        and all(
            item.boundary == EvidenceBoundary.HISTORICAL_FACT
            for item in solution.historical_evidence
        )
        and all(
            item.boundary == EvidenceBoundary.ENTERPRISE_CAPABILITY
            for item in solution.capability_composition
        )
        and all(
            item.boundary == EvidenceBoundary.PENDING_CONFIRMATION
            for item in solution.pending_confirmations
        )
    )


def evaluate_solution(
    case: ResolvedEvaluationCase,
    raw_solution: dict[str, Any],
    duration_ms: float,
) -> EvaluationOutcome:
    try:
        solution = Solution.model_validate(raw_solution)
    except Exception as exc:
        return EvaluationOutcome(
            case_id=case.spec.case_id,
            category=case.spec.category,
            schema_passed=False,
            citations_supported=False,
            boundary_correct=False,
            forbidden_claims_absent=False,
            manual_edit_count=case.spec.manual_edit_count,
            duration_ms=duration_ms,
            error=f"schema_validation_error: {str(exc)[:500]}",
        )

    text = solution_text(solution)
    expected_assets = set(case.spec.expected_asset_ids)
    asset_hit = expected_assets.issubset(cited_asset_ids(solution)) if expected_assets else None
    refusal_markers = ("无足够依据", "当前企业资产未支持", "不能按当前要求")
    refused = any(marker in text for marker in refusal_markers)
    return EvaluationOutcome(
        case_id=case.spec.case_id,
        category=case.spec.category,
        schema_passed=True,
        expected_asset_hit=asset_hit,
        citations_supported=citations_are_supported(solution, case.retrieval_snapshot),
        no_basis_refused=refused if case.spec.expected_refusal else None,
        boundary_correct=boundaries_are_correct(solution),
        forbidden_claims_absent=not any(
            forbidden in text for forbidden in case.spec.forbidden_claims
        ),
        manual_edit_count=case.spec.manual_edit_count,
        duration_ms=duration_ms,
    )


def error_outcome(
    case: ResolvedEvaluationCase,
    exc: Exception,
    duration_ms: float,
) -> EvaluationOutcome:
    return EvaluationOutcome(
        case_id=case.spec.case_id,
        category=case.spec.category,
        schema_passed=False,
        citations_supported=False,
        boundary_correct=False,
        forbidden_claims_absent=False,
        manual_edit_count=case.spec.manual_edit_count,
        duration_ms=duration_ms,
        error=f"generation_error: {type(exc).__name__}: {str(exc)[:500]}",
    )


def optional_rate(values: list[bool | None]) -> float | None:
    selected = [value for value in values if value is not None]
    return sum(selected) / len(selected) if selected else None


def summarize_outcomes(outcomes: list[EvaluationOutcome]) -> EvaluationSummary:
    total = len(outcomes)
    divisor = total or 1
    manual_counts = [
        outcome.manual_edit_count for outcome in outcomes if outcome.manual_edit_count is not None
    ]
    return EvaluationSummary(
        total_cases=total,
        passed_cases=sum(outcome.passed for outcome in outcomes),
        schema_pass_rate=sum(outcome.schema_passed for outcome in outcomes) / divisor,
        asset_hit_rate=optional_rate([outcome.expected_asset_hit for outcome in outcomes]),
        citation_support_rate=sum(outcome.citations_supported for outcome in outcomes) / divisor,
        refusal_pass_rate=optional_rate([outcome.no_basis_refused for outcome in outcomes]),
        boundary_pass_rate=sum(outcome.boundary_correct for outcome in outcomes) / divisor,
        average_duration_ms=(sum(outcome.duration_ms for outcome in outcomes) / divisor),
        manual_edit_count_total=sum(manual_counts) if manual_counts else None,
    )


async def run_evaluation_suite(
    suite: EvaluationSuiteSpec,
    cases: list[ResolvedEvaluationCase],
    engine: SolutionEngine,
    *,
    model_version: str,
    prompt_version: str,
) -> EvaluationReport:
    outcomes: list[EvaluationOutcome] = []
    for case in cases:
        started = perf_counter()
        try:
            result = await engine.generate_solution(
                case.context.model_dump(mode="json"),
                case.retrieval_snapshot.model_dump(mode="json"),
            )
            duration_ms = (perf_counter() - started) * 1000
            outcomes.append(evaluate_solution(case, result, duration_ms))
        except Exception as exc:  # one bad case must not hide the remaining regression results
            duration_ms = (perf_counter() - started) * 1000
            outcomes.append(error_outcome(case, exc, duration_ms))
    return EvaluationReport(
        suite_version=suite.suite_version,
        run_at=datetime.now(UTC),
        model_version=model_version,
        prompt_version=prompt_version,
        outcomes=outcomes,
        summary=summarize_outcomes(outcomes),
    )
