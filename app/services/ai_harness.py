import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.ai import AIEngine
from app.contracts.presentation import SlidePlanner
from app.core.errors import ErrorCode
from app.db.models import ProcessStatus
from app.db.repositories import SolutionRunRepository
from app.schemas.presentation import (
    PlanningFact,
    SlidePlanData,
    SlidePlanningContext,
    SlidePlanningStyleConstraints,
)

logger = logging.getLogger(__name__)

REQUIRED_SOLUTION_SECTIONS = frozenset(
    {
        "requirement_understanding",
        "initial_recommendations",
        "historical_evidence",
        "capability_composition",
        "prerequisites_and_risks",
        "pending_confirmations",
        "sources",
        "suggested_questions",
    }
)


class PresentationPlanningUnavailable(RuntimeError):
    pass


class PresentationPlanningHarness:
    """Retry and validate Molly planning without mutating SolutionRun state."""

    def __init__(
        self,
        planner: SlidePlanner,
        *,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.1,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.planner = planner
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

    async def run_slide_planning(
        self,
        context: SlidePlanningContext,
        fact_catalog: tuple[PlanningFact, ...],
        style_constraints: SlidePlanningStyleConstraints,
    ) -> SlidePlanData:
        allowed_claims = {fact.claim_id for fact in fact_catalog}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = await self.planner.plan_slides(context, fact_catalog, style_constraints)
                plan = SlidePlanData.model_validate(raw)
                self._validate_plan(plan, context, allowed_claims, style_constraints)
                return plan
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "AI slide planning failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    type(exc).__name__,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay_seconds * (2**attempt))
        raise PresentationPlanningUnavailable("slide planning retries exhausted") from last_error

    @staticmethod
    def _validate_plan(
        plan: SlidePlanData,
        context: SlidePlanningContext,
        allowed_claims: set[uuid.UUID],
        style_constraints: SlidePlanningStyleConstraints,
    ) -> None:
        if plan.presentation_id != context.presentation_id:
            raise ValueError("slide plan presentation_id mismatch")
        if len(plan.pages) > style_constraints.max_pages:
            raise ValueError("slide plan exceeds max_pages")
        allowed_layouts = set(style_constraints.allowed_layout_tokens)
        for page in plan.pages:
            if page.layout_token not in allowed_layouts:
                raise ValueError("slide plan uses a layout outside style constraints")
            if len(page.components) > style_constraints.max_components_per_page:
                raise ValueError("slide plan exceeds component capacity")
            for component in page.components:
                unknown = set(component.claim_ids) - allowed_claims
                if unknown:
                    raise ValueError("slide plan references claims outside FactLedger")


class AIHarness:
    def __init__(
        self,
        engine: AIEngine,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.1,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.engine = engine
        self.session = session
        self.runs = SolutionRunRepository(session, workspace_id)
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

    async def run_solution_generation(
        self,
        run_id: uuid.UUID,
        context: dict,
        retrieval_snapshot: dict,
    ) -> dict | None:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await self.engine.generate_solution(context, retrieval_snapshot)
                self._validate_solution(result)
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "AI solution generation failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    type(exc).__name__,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay_seconds * (2**attempt))

        await self._mark_failed(run_id, last_error)
        return None

    @staticmethod
    def _validate_solution(result: object) -> None:
        if not isinstance(result, dict):
            raise ValueError("AI solution must be a JSON object")
        missing = REQUIRED_SOLUTION_SECTIONS.difference(result)
        if missing:
            raise ValueError(f"AI solution is missing sections: {', '.join(sorted(missing))}")
        invalid = [
            name for name in REQUIRED_SOLUTION_SECTIONS if not isinstance(result[name], list)
        ]
        if invalid:
            raise ValueError(f"AI solution sections must be arrays: {', '.join(sorted(invalid))}")

    async def _mark_failed(self, run_id: uuid.UUID, last_error: Exception | None) -> None:
        try:
            run = await self.runs.get(run_id)
            if run is None:
                logger.error("Cannot mark missing SolutionRun %s as failed", run_id)
                return
            run.status = ProcessStatus.FAILED
            run.error_code = ErrorCode.MODEL_TEMPORARILY_UNAVAILABLE.value
            run.retryable = True
            run.completed_at = datetime.now(UTC)
            await self.session.commit()
        except Exception:
            logger.exception(
                "Failed to persist AI failure boundary for SolutionRun %s (cause: %r)",
                run_id,
                last_error,
            )
            await self.session.rollback()
