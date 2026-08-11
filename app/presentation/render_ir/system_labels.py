import re

from app.presentation.render_ir.models import (
    FactTrace,
    SystemLabelTrace,
    TextProvenance,
)
from app.schemas.presentation import FactBinding, FactLedger, PresentationSpecData

SYSTEM_LABEL_CATALOG_VERSION = "presentation-labels-v1"

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

FIXED_LABELS = {
    "metric.label": "已验证关键指标",
    "comparison.heading": "已验证事实对比",
    "timeline.heading": "实施事实顺序",
    "process.heading": "可信交付步骤",
    "source_list.heading": "已授权来源",
}


class SystemLabelCatalog:
    version = SYSTEM_LABEL_CATALOG_VERSION

    def build_provenance(
        self, spec: PresentationSpecData, ledger: FactLedger
    ) -> dict[str, TextProvenance]:
        facts = {fact.claim_id: fact for fact in ledger.facts}
        result: dict[str, TextProvenance] = {}
        for slide in spec.slides:
            for component in slide.components:
                prefix = str(component.component_id)
                if component.component_type == "title":
                    purpose = self._purpose_for_title(component.text)
                    result[f"{prefix}:text"] = self._system(f"slide.{purpose}.title")
                elif component.component_type == "evidence_card":
                    result[f"{prefix}:heading"] = self._ledger_label(
                        component.heading, component.fact_binding, facts
                    )
                elif component.component_type == "metric":
                    self._require_fixed("metric.label", component.label)
                    result[f"{prefix}:label"] = self._system("metric.label")
                elif component.component_type == "comparison":
                    self._require_fixed("comparison.heading", component.heading)
                    result[f"{prefix}:heading"] = self._system("comparison.heading")
                    for side, name in ((component.left, "left"), (component.right, "right")):
                        self._require_pattern(side.label, r"对比项 [1-9][0-9]*")
                        result[f"{prefix}:{name}.0.label"] = self._system(
                            f"comparison.item.{side.label.rsplit(' ', 1)[-1]}"
                        )
                elif component.component_type in {"timeline", "process"}:
                    heading_token = f"{component.component_type}.heading"
                    self._require_fixed(heading_token, component.heading)
                    result[f"{prefix}:heading"] = self._system(heading_token)
                    items = (
                        component.items
                        if component.component_type == "timeline"
                        else component.steps
                    )
                    word = "阶段" if component.component_type == "timeline" else "步骤"
                    for index, item in enumerate(items):
                        self._require_pattern(item.label, rf"{word} [1-9][0-9]*")
                        number = item.label.rsplit(" ", 1)[-1]
                        result[f"{prefix}:items.{index}.label"] = self._system(
                            f"{component.component_type}.item.{number}"
                        )
                elif component.component_type == "source_list":
                    self._require_fixed("source_list.heading", component.heading)
                    result[f"{prefix}:heading"] = self._system("source_list.heading")
                    for index, source in enumerate(component.sources):
                        match = re.fullmatch(
                            r"来源 ([1-9][0-9]*)（版本 ([1-9][0-9]*)）", source.label
                        )
                        if match is None:
                            raise ValueError("source label is outside the system label catalog")
                        fact = facts.get(source.fact_binding.claim_id)
                        if fact is None:
                            raise ValueError("source label references an unknown claim")
                        expected_versions = {
                            item.source_version
                            for item in fact.evidence
                            if item.source_id == source.source_id
                        }
                        if int(match.group(2)) not in expected_versions:
                            raise ValueError("source label version is outside FactLedger")
                        result[f"{prefix}:sources.{index}.label"] = self._system(
                            f"source.item.{match.group(1)}.version"
                        )
        return result

    def _ledger_label(self, text: str, binding: FactBinding, facts: dict) -> TextProvenance:
        fact = facts.get(binding.claim_id)
        if fact is None or text not in fact.allowed_labels:
            raise ValueError("ledger label is not frozen in FactLedger")
        return TextProvenance(
            content_origin="ledger_label",
            fact_trace=FactTrace(
                claim_id=binding.claim_id,
                claim_key=binding.claim_key,
                evidence_ids=tuple(binding.evidence_ids),
                source_ids=tuple(binding.source_ids),
                content_mode="label_only",
            ),
        )

    @staticmethod
    def _purpose_for_title(text: str) -> str:
        for purpose, expected in PURPOSE_TITLES.items():
            if text == expected:
                return purpose
        raise ValueError("slide title is outside the system label catalog")

    @staticmethod
    def _require_fixed(token: str, text: str) -> None:
        if FIXED_LABELS[token] != text:
            raise ValueError(f"system label does not match catalog token: {token}")

    @staticmethod
    def _require_pattern(text: str, pattern: str) -> None:
        if re.fullmatch(pattern, text) is None:
            raise ValueError("dynamic label is outside the system label catalog")

    def _system(self, token: str) -> TextProvenance:
        return TextProvenance(
            content_origin="system_label",
            system_label_trace=SystemLabelTrace(
                token=token,
                catalog_version=self.version,
            ),
        )
