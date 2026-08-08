from typing import Protocol

from app.ai.pipelines.solution import finalize_solution_result
from app.ai.prompts.revision import REVISION_SYSTEM_PROMPT, build_revision_user_prompt
from app.ai.schemas import QualityReport, RetrievalSnapshot, Solution, SolutionContext


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


async def revise_solution(
    context: SolutionContext,
    retrieval_snapshot: RetrievalSnapshot,
    previous_solution: Solution,
    quality_report: QualityReport,
    model_client: JsonModelClient,
) -> Solution:
    if quality_report.passed:
        raise ValueError("a passed solution does not need revision")

    result = await model_client.generate_json(
        REVISION_SYSTEM_PROMPT,
        build_revision_user_prompt(
            context.model_dump_json(indent=2),
            retrieval_snapshot.model_dump_json(indent=2),
            previous_solution.model_dump_json(indent=2),
            quality_report.model_dump_json(indent=2),
        ),
    )
    return finalize_solution_result(context, retrieval_snapshot, result)
