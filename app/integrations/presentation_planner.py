import re
import uuid
from typing import Any

from app.schemas.presentation import (
    PlanningFact,
    SlidePlanComponent,
    SlidePlanData,
    SlidePlanningContext,
    SlidePlanningStyleConstraints,
    SlidePlanPage,
)


class MockSlidePlanner:
    """Deterministic planner that selects facts but never copies their text into its output."""

    async def plan_slides(
        self,
        context: SlidePlanningContext,
        fact_catalog: tuple[PlanningFact, ...],
        style_constraints: SlidePlanningStyleConstraints,
    ) -> SlidePlanData:
        if not fact_catalog:
            raise ValueError("fact catalog must not be empty")
        allowed = set(style_constraints.allowed_layout_tokens)
        pages: list[SlidePlanPage] = []

        if "cover" in allowed:
            pages.append(self._page(context, "cover", "cover", [], 0, 120))

        preferences = [
            token
            for token in style_constraints.preferred_layout_tokens
            if token in {"evidence_grid", "three_cards", "two_column", "title_body"}
        ]
        evidence_layout = next(
            (
                token
                for token in [
                    *preferences,
                    "evidence_grid",
                    "three_cards",
                    "two_column",
                    "title_body",
                ]
                if token in allowed
            ),
            None,
        )
        if evidence_layout is None:
            raise ValueError("style constraints do not allow an evidence-compatible layout")
        evidence_limit = min(
            {
                "evidence_grid": 4,
                "three_cards": 3,
                "two_column": 2,
                "title_body": 1,
            }[evidence_layout],
            style_constraints.max_components_per_page,
        )
        evidence_facts = fact_catalog[:evidence_limit]
        evidence_components = [
            self._component(context, "evidence_card", [fact.claim_id], index)
            for index, fact in enumerate(evidence_facts, start=1)
        ]
        pages.append(
            self._page(
                context,
                "evidence",
                evidence_layout,
                evidence_components,
                1,
                2_000,
            )
        )

        numeric_fact = next(
            (fact for fact in fact_catalog if re.search(r"\d", fact.verbatim_text)), None
        )
        if numeric_fact is not None and "metric_highlight" in allowed:
            pages.append(
                self._page(
                    context,
                    "key_metric",
                    "metric_highlight",
                    [self._component(context, "metric", [numeric_fact.claim_id], 10)],
                    2,
                    420,
                )
            )

        if len(fact_catalog) >= 2 and "process" in allowed:
            claims = [fact.claim_id for fact in fact_catalog[:5]]
            pages.append(
                self._page(
                    context,
                    "delivery_process",
                    "process",
                    [self._component(context, "process", claims, 20)],
                    3,
                    1_600,
                )
            )

        if "source_list" in allowed:
            claims = [fact.claim_id for fact in fact_catalog[:6]]
            pages.append(
                self._page(
                    context,
                    "sources",
                    "source_list",
                    [self._component(context, "source_list", claims, 30)],
                    4,
                    1_200,
                )
            )

        return SlidePlanData(
            presentation_id=context.presentation_id,
            pages=pages[: style_constraints.max_pages],
        )

    @staticmethod
    def _component(context, component_type, claim_ids, index) -> SlidePlanComponent:
        return SlidePlanComponent(
            component_id=uuid.uuid5(context.presentation_id, f"component:{component_type}:{index}"),
            component_type=component_type,
            claim_ids=claim_ids,
            priority=1,
        )

    @staticmethod
    def _page(context, purpose, layout_token, components, index, budget) -> SlidePlanPage:
        return SlidePlanPage(
            slide_id=uuid.uuid5(context.presentation_id, f"slide:{purpose}:{index}"),
            purpose=purpose,
            layout_token=layout_token,
            components=components,
            character_budget=budget,
            allow_pagination=True,
        )


class AIEngineSlidePlanner:
    """Live Molly adapter; the AI engine owns prompts while the backend owns validation."""

    def __init__(self, engine: object) -> None:
        self.engine = engine

    async def plan_slides(
        self,
        context: SlidePlanningContext,
        fact_catalog: tuple[PlanningFact, ...],
        style_constraints: SlidePlanningStyleConstraints,
    ) -> SlidePlanData | dict[str, Any]:
        operation = getattr(self.engine, "plan_slides", None)
        if operation is None:
            raise RuntimeError("live AI engine does not implement the frozen plan_slides contract")
        return await operation(
            context.model_dump(mode="json"),
            [fact.model_dump(mode="json") for fact in fact_catalog],
            style_constraints.model_dump(mode="json"),
        )
