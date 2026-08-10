import hashlib
import json
import uuid
from dataclasses import dataclass

from app.schemas.presentation import (
    EvidenceGuardSnapshot,
    FactBinding,
    FactLedger,
    GuardFailure,
    PositionedPresentationSpec,
    PresentationSpecData,
    SlideSchema,
    ValidationReport,
)


@dataclass(frozen=True)
class _BindingEntry:
    binding: FactBinding
    content: str | None
    source_id: uuid.UUID | None = None


class EvidenceGuard:
    def validate_bindings(
        self, spec: PresentationSpecData, ledger: FactLedger
    ) -> tuple[ValidationReport, EvidenceGuardSnapshot]:
        reports = [self.validate(slide, ledger) for slide in spec.slides]
        report = self._merge_reports(reports)
        fingerprints: dict = {}
        claim_ids: dict = {}
        if report.passed:
            for slide in spec.slides:
                for component in slide.components:
                    entries = self._binding_entries(component)
                    if not entries:
                        continue
                    fingerprints[component.component_id] = self._fingerprint(component)
                    claim_ids[component.component_id] = tuple(
                        entry.binding.claim_id for entry in entries
                    )
        return report, EvidenceGuardSnapshot(
            component_fingerprints=fingerprints,
            component_claim_ids=claim_ids,
        )

    def validate_positioned_spec(
        self,
        spec: PositionedPresentationSpec,
        ledger: FactLedger,
        snapshot: EvidenceGuardSnapshot,
    ) -> ValidationReport:
        slides = []
        positioned_fingerprints = {}
        for slide in spec.slides:
            components = [item.component for item in slide.components]
            slides.append(
                SlideSchema(
                    slide_id=slide.slide_id,
                    layout_token=slide.layout_token,
                    components=components,
                )
            )
            for component in components:
                if self._binding_entries(component):
                    positioned_fingerprints[component.component_id] = self._fingerprint(component)

        reports = [self.validate(slide, ledger) for slide in slides]
        failures = [failure for report in reports for failure in report.failures]
        for component_id, expected in snapshot.component_fingerprints.items():
            actual = positioned_fingerprints.get(component_id)
            claim_id = snapshot.component_claim_ids[component_id][0]
            if actual is None:
                failures.append(
                    GuardFailure(
                        code="BOUND_COMPONENT_MISSING",
                        component_id=component_id,
                        claim_id=claim_id,
                        message="布局结果丢失了已通过预检的事实组件",
                    )
                )
            elif actual != expected:
                failures.append(
                    GuardFailure(
                        code="CONTENT_FINGERPRINT_MISMATCH",
                        component_id=component_id,
                        claim_id=claim_id,
                        message="布局前后组件事实内容或绑定发生变化",
                    )
                )
        added_ids = set(positioned_fingerprints) - set(snapshot.component_fingerprints)
        for component_id in added_ids:
            component = next(
                component
                for slide in slides
                for component in slide.components
                if component.component_id == component_id
            )
            entries = self._binding_entries(component)
            failures.append(
                GuardFailure(
                    code="BOUND_COMPONENT_ADDED",
                    component_id=component_id,
                    claim_id=entries[0].binding.claim_id,
                    message="布局结果新增了未经过预检的事实组件",
                )
            )

        return ValidationReport(
            passed=not failures,
            checked_components=sum(report.checked_components for report in reports),
            failures=tuple(failures),
        )

    def validate(self, schema: SlideSchema, ledger: FactLedger) -> ValidationReport:
        facts = {fact.claim_id: fact for fact in ledger.facts}
        failures: list[GuardFailure] = []
        checked_components = 0

        for component in schema.components:
            for entry in self._binding_entries(component):
                checked_components += 1
                self._validate_entry(component.component_id, entry, facts, failures)

        return ValidationReport(
            passed=not failures,
            checked_components=checked_components,
            failures=tuple(failures),
        )

    @staticmethod
    def _verbatim_content(component: object) -> str | None:
        value = getattr(component, "value", None)
        if isinstance(value, str):
            return value
        body = getattr(component, "body", None)
        if isinstance(body, str):
            return body
        text = getattr(component, "text", None)
        return text if isinstance(text, str) else None

    def _binding_entries(self, component: object) -> list[_BindingEntry]:
        binding = getattr(component, "fact_binding", None)
        if isinstance(binding, FactBinding):
            return [_BindingEntry(binding=binding, content=self._verbatim_content(component))]

        entries: list[_BindingEntry] = []
        for attribute in ("left", "right"):
            item = getattr(component, attribute, None)
            if item is not None:
                entries.append(_BindingEntry(binding=item.fact_binding, content=item.text))
        for attribute in ("items", "steps"):
            for item in getattr(component, attribute, ()):
                entries.append(_BindingEntry(binding=item.fact_binding, content=item.text))
        for item in getattr(component, "sources", ()):
            entries.append(
                _BindingEntry(
                    binding=item.fact_binding,
                    content=None,
                    source_id=item.source_id,
                )
            )
        return entries

    @staticmethod
    def _validate_entry(component_id, entry, facts, failures) -> None:
        binding = entry.binding
        fact = facts.get(binding.claim_id)
        if fact is None:
            failures.append(
                GuardFailure(
                    code="UNKNOWN_CLAIM",
                    component_id=component_id,
                    claim_id=binding.claim_id,
                    message="组件引用了事实账本中不存在的 claim_id",
                )
            )
            return

        if binding.claim_key != fact.claim_key:
            failures.append(
                GuardFailure(
                    code="CLAIM_KEY_MISMATCH",
                    component_id=component_id,
                    claim_id=binding.claim_id,
                    message="claim_key 与事实账本不一致",
                )
            )

        allowed_evidence = {item.evidence_id for item in fact.evidence}
        if set(binding.evidence_ids) - allowed_evidence:
            failures.append(
                GuardFailure(
                    code="UNKNOWN_EVIDENCE",
                    component_id=component_id,
                    claim_id=binding.claim_id,
                    message="组件引用了不属于该 Claim 的 Evidence",
                )
            )

        allowed_sources = {item.source_id for item in fact.evidence}
        if set(binding.source_ids) - allowed_sources or (
            entry.source_id is not None and entry.source_id not in allowed_sources
        ):
            failures.append(
                GuardFailure(
                    code="SOURCE_OUT_OF_BOUNDS",
                    component_id=component_id,
                    claim_id=binding.claim_id,
                    message="组件引用的 Source 超出该 Claim 的证据边界",
                )
            )

        if binding.content_mode == "verbatim":
            if entry.content is None:
                failures.append(
                    GuardFailure(
                        code="VERBATIM_CONTENT_MISSING",
                        component_id=component_id,
                        claim_id=binding.claim_id,
                        message="原词引用组件没有可校验的内容字段",
                    )
                )
            elif entry.content != fact.verbatim_text:
                failures.append(
                    GuardFailure(
                        code="VERBATIM_CONTENT_MISMATCH",
                        component_id=component_id,
                        claim_id=binding.claim_id,
                        message="组件内容与事实账本原文不完全一致",
                    )
                )

    @staticmethod
    def _fingerprint(component: object) -> str:
        payload = component.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _merge_reports(reports: list[ValidationReport]) -> ValidationReport:
        failures = tuple(failure for report in reports for failure in report.failures)
        return ValidationReport(
            passed=not failures,
            checked_components=sum(report.checked_components for report in reports),
            failures=failures,
        )
