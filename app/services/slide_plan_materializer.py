import uuid

from app.schemas.presentation import (
    BoundFactItem,
    BoundSourceItem,
    ComparisonComponent,
    EvidenceCardComponent,
    FactAtom,
    FactBinding,
    FactLedger,
    KeyMessageComponent,
    MetricComponent,
    PresentationSpecData,
    ProcessComponent,
    SlidePlanComponent,
    SlidePlanData,
    SlideSchema,
    SourceListComponent,
    TimelineComponent,
    TitleComponent,
)

PURPOSE_TITLES = {
    "cover": "企业数字化转型可信方案",
    "executive_summary": "方案核心结论",
    "key_metric": "已验证关键指标",
    "comparison": "方案事实对比",
    "implementation_timeline": "分阶段实施路线",
    "delivery_process": "可信交付流程",
    "evidence": "已验证事实与能力",
    "sources": "方案事实来源",
}


class SlidePlanMaterializer:
    """Resolve claim references into verbatim components without AI-authored facts."""

    def materialize(self, plan: SlidePlanData, ledger: FactLedger) -> PresentationSpecData:
        facts = {fact.claim_id: fact for fact in ledger.facts}
        slides = []
        for page in plan.pages:
            title = TitleComponent(
                component_id=uuid.uuid5(page.slide_id, "title"),
                text=PURPOSE_TITLES[page.purpose],
            )
            components = [title]
            components.extend(
                self._materialize_component(component, facts) for component in page.components
            )
            actual_characters = sum(
                len(facts[claim_id].verbatim_text)
                for component in page.components
                if component.component_type != "source_list"
                for claim_id in component.claim_ids
            )
            if actual_characters > page.character_budget:
                raise ValueError("materialized facts exceed SlidePlan character_budget")
            slides.append(
                SlideSchema(
                    slide_id=page.slide_id,
                    layout_token=page.layout_token,
                    components=components,
                )
            )
        return PresentationSpecData(
            schema_version="slide-schema-v1",
            presentation_id=plan.presentation_id,
            slides=slides,
        )

    def _materialize_component(
        self, component: SlidePlanComponent, facts: dict[uuid.UUID, FactAtom]
    ):
        selected = [self._fact(facts, claim_id) for claim_id in component.claim_ids]
        first = selected[0]
        if component.component_type == "key_message":
            return KeyMessageComponent(
                component_id=component.component_id,
                text=first.verbatim_text,
                fact_binding=self._binding(first, "verbatim"),
            )
        if component.component_type == "evidence_card":
            return EvidenceCardComponent(
                component_id=component.component_id,
                heading=first.claim_key,
                body=first.verbatim_text,
                fact_binding=self._binding(first, "verbatim"),
            )
        if component.component_type == "metric":
            return MetricComponent(
                component_id=component.component_id,
                label="已验证关键指标",
                value=first.verbatim_text,
                fact_binding=self._binding(first, "verbatim"),
            )
        if component.component_type == "comparison":
            return ComparisonComponent(
                component_id=component.component_id,
                heading="已验证事实对比",
                left=self._bound_item(selected[0], "对比项 1", component.component_id, 0),
                right=self._bound_item(selected[1], "对比项 2", component.component_id, 1),
            )
        if component.component_type == "timeline":
            return TimelineComponent(
                component_id=component.component_id,
                heading="实施事实顺序",
                items=[
                    self._bound_item(fact, f"阶段 {index}", component.component_id, index)
                    for index, fact in enumerate(selected, start=1)
                ],
            )
        if component.component_type == "process":
            return ProcessComponent(
                component_id=component.component_id,
                heading="可信交付步骤",
                steps=[
                    self._bound_item(fact, f"步骤 {index}", component.component_id, index)
                    for index, fact in enumerate(selected, start=1)
                ],
            )
        if component.component_type == "source_list":
            sources = []
            seen_sources = set()
            for fact in selected:
                for evidence in fact.evidence:
                    if evidence.source_id in seen_sources:
                        continue
                    seen_sources.add(evidence.source_id)
                    sources.append(
                        BoundSourceItem(
                            item_id=uuid.uuid5(
                                component.component_id, f"source:{evidence.source_id}"
                            ),
                            label=f"来源 {len(sources) + 1}（版本 {evidence.source_version}）",
                            source_id=evidence.source_id,
                            fact_binding=FactBinding(
                                claim_id=fact.claim_id,
                                claim_key=fact.claim_key,
                                evidence_ids=[
                                    item.evidence_id
                                    for item in fact.evidence
                                    if item.source_id == evidence.source_id
                                ],
                                source_ids=[evidence.source_id],
                                content_mode="label_only",
                            ),
                        )
                    )
            return SourceListComponent(
                component_id=component.component_id,
                heading="已授权来源",
                sources=sources,
            )
        raise ValueError(f"unsupported planned component: {component.component_type}")

    @staticmethod
    def _fact(facts: dict[uuid.UUID, FactAtom], claim_id: uuid.UUID) -> FactAtom:
        try:
            return facts[claim_id]
        except KeyError as exc:
            raise ValueError("SlidePlan references a claim outside FactLedger") from exc

    @staticmethod
    def _binding(fact: FactAtom, mode: str) -> FactBinding:
        return FactBinding(
            claim_id=fact.claim_id,
            claim_key=fact.claim_key,
            evidence_ids=[item.evidence_id for item in fact.evidence],
            source_ids=list(dict.fromkeys(item.source_id for item in fact.evidence)),
            content_mode=mode,
        )

    def _bound_item(
        self, fact: FactAtom, label: str, component_id: uuid.UUID, index: int
    ) -> BoundFactItem:
        return BoundFactItem(
            item_id=uuid.uuid5(component_id, f"item:{index}:{fact.claim_id}"),
            label=label,
            text=fact.verbatim_text,
            fact_binding=self._binding(fact, "verbatim"),
        )
