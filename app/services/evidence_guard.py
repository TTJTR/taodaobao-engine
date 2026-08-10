import hashlib
import json

from app.schemas.presentation import (
    EvidenceGuardSnapshot,
    FactLedger,
    GuardFailure,
    PositionedPresentationSpec,
    PresentationSpecData,
    SlideSchema,
    ValidationReport,
)


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
                    binding = getattr(component, "fact_binding", None)
                    if binding is None:
                        continue
                    fingerprints[component.component_id] = self._fingerprint(component)
                    claim_ids[component.component_id] = binding.claim_id
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
                if getattr(component, "fact_binding", None) is not None:
                    positioned_fingerprints[component.component_id] = self._fingerprint(component)

        reports = [self.validate(slide, ledger) for slide in slides]
        failures = [failure for report in reports for failure in report.failures]
        for component_id, expected in snapshot.component_fingerprints.items():
            actual = positioned_fingerprints.get(component_id)
            claim_id = snapshot.component_claim_ids[component_id]
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
            failures.append(
                GuardFailure(
                    code="BOUND_COMPONENT_ADDED",
                    component_id=component_id,
                    claim_id=component.fact_binding.claim_id,
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
            binding = getattr(component, "fact_binding", None)
            if binding is None:
                continue
            checked_components += 1
            fact = facts.get(binding.claim_id)
            if fact is None:
                failures.append(
                    GuardFailure(
                        code="UNKNOWN_CLAIM",
                        component_id=component.component_id,
                        claim_id=binding.claim_id,
                        message="组件引用了事实账本中不存在的 claim_id",
                    )
                )
                continue

            if binding.claim_key != fact.claim_key:
                failures.append(
                    GuardFailure(
                        code="CLAIM_KEY_MISMATCH",
                        component_id=component.component_id,
                        claim_id=binding.claim_id,
                        message="claim_key 与事实账本不一致",
                    )
                )

            allowed_evidence = {item.evidence_id for item in fact.evidence}
            unknown_evidence = set(binding.evidence_ids) - allowed_evidence
            if unknown_evidence:
                failures.append(
                    GuardFailure(
                        code="UNKNOWN_EVIDENCE",
                        component_id=component.component_id,
                        claim_id=binding.claim_id,
                        message="组件引用了不属于该 Claim 的 Evidence",
                    )
                )

            allowed_sources = {item.source_id for item in fact.evidence}
            unknown_sources = set(binding.source_ids) - allowed_sources
            if unknown_sources:
                failures.append(
                    GuardFailure(
                        code="SOURCE_OUT_OF_BOUNDS",
                        component_id=component.component_id,
                        claim_id=binding.claim_id,
                        message="组件引用的 Source 超出该 Claim 的证据边界",
                    )
                )

            if binding.content_mode == "verbatim":
                content = self._verbatim_content(component)
                if content is None:
                    failures.append(
                        GuardFailure(
                            code="VERBATIM_CONTENT_MISSING",
                            component_id=component.component_id,
                            claim_id=binding.claim_id,
                            message="原词引用组件没有可校验的内容字段",
                        )
                    )
                elif content != fact.verbatim_text:
                    failures.append(
                        GuardFailure(
                            code="VERBATIM_CONTENT_MISMATCH",
                            component_id=component.component_id,
                            claim_id=binding.claim_id,
                            message="组件内容与事实账本原文不完全一致",
                        )
                    )

        return ValidationReport(
            passed=not failures,
            checked_components=checked_components,
            failures=tuple(failures),
        )

    @staticmethod
    def _verbatim_content(component: object) -> str | None:
        body = getattr(component, "body", None)
        if isinstance(body, str):
            return body
        text = getattr(component, "text", None)
        return text if isinstance(text, str) else None

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
