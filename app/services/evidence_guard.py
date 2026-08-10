from app.schemas.presentation import (
    FactLedger,
    GuardFailure,
    SlideSchema,
    ValidationReport,
)


class EvidenceGuard:
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
