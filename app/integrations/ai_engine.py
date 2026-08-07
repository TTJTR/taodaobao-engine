from typing import Any

from app.contracts.ai import AIEngine


class MockAIEngine(AIEngine):
    async def extract_profile(
        self, raw_text: str, source_ids: list[str]
    ) -> dict[str, Any]:
        return {"background": raw_text, "source_ids": source_ids}

    async def extract_experience(
        self, raw_text: str, source_id: str
    ) -> dict[str, Any]:
        return {"solution": raw_text, "source_id": source_id}

    async def extract_capabilities(
        self, raw_text: str, source_id: str
    ) -> list[dict[str, Any]]:
        return [{"description": raw_text, "source_id": source_id}]

    async def extract_search_intent(
        self, context: dict[str, Any]
    ) -> dict[str, Any]:
        return {"keywords": [str(context.get("requirement", ""))]}

    async def generate_solution(
        self,
        context: dict[str, Any],
        retrieval_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        requirement = str(context.get("requirement", ""))
        experiences = retrieval_snapshot.get("experiences", [])
        capabilities = retrieval_snapshot.get("capabilities", [])
        return {
            "requirement_understanding": [
                self._cited(requirement, "pending_confirmation")
            ],
            "initial_recommendations": [
                self._cited(
                    "优先以小范围旁路试点验证价值，再根据验收结果扩展。",
                    "ai_inference",
                )
            ],
            "historical_evidence": [
                self._asset_citation(item, "solution", "historical_fact")
                for item in experiences
            ],
            "capability_composition": [
                self._asset_citation(item, "description", "enterprise_capability")
                for item in capabilities
            ],
            "prerequisites_and_risks": [
                self._cited(
                    "需确认数据权限、接口条件、样本质量与验收口径。",
                    "pending_confirmation",
                )
            ],
            "pending_confirmations": [
                self._cited(
                    "试点范围、周期、预算和最终决策人仍需客户确认。",
                    "pending_confirmation",
                )
            ],
            "sources": [
                {
                    "asset_id": item["id"],
                    "source_id": item["source_id"],
                    "type": kind,
                }
                for kind, items in (
                    ("experience", experiences),
                    ("capability", capabilities),
                )
                for item in items
            ],
            "suggested_questions": [
                "第一阶段试点的业务范围是什么？",
                "现有系统可以开放哪些数据与接口？",
                "客户将用哪些指标验收？",
            ],
        }

    @staticmethod
    def _cited(text: str, boundary: str) -> dict[str, Any]:
        return {
            "text": text,
            "boundary": boundary,
            "asset_id": None,
            "source_id": None,
        }

    @classmethod
    def _asset_citation(
        cls, item: dict[str, Any], preferred_field: str, boundary: str
    ) -> dict[str, Any]:
        data = item.get("data", {})
        citation = cls._cited(
            str(data.get(preferred_field, data.get("name", "已校验资产"))), boundary
        )
        citation["asset_id"] = item.get("id")
        citation["source_id"] = item.get("source_id")
        return citation
