from typing import Protocol

from app.schemas.presentation import (
    PlanningFact,
    SlidePlanData,
    SlidePlanningContext,
    SlidePlanningStyleConstraints,
)


class SlidePlanner(Protocol):
    async def plan_slides(
        self,
        context: SlidePlanningContext,
        fact_catalog: tuple[PlanningFact, ...],
        style_constraints: SlidePlanningStyleConstraints,
    ) -> SlidePlanData | dict: ...
