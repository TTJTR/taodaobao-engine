from app.ai.pipelines.quality import JsonModelClient as QualityModelClient
from app.ai.pipelines.quality import quality_check_solution
from app.ai.pipelines.revision import JsonModelClient as RevisionModelClient
from app.ai.pipelines.revision import revise_solution
from app.ai.pipelines.solution import JsonModelClient as SolutionModelClient
from app.ai.pipelines.solution import generate_solution
from app.ai.schemas import (
    QualityAttempt,
    RetrievalSnapshot,
    SolutionContext,
    VerifiedSolutionResult,
)

MAX_SOLUTION_REVISIONS = 2


async def generate_verified_solution(
    context: SolutionContext,
    retrieval_snapshot: RetrievalSnapshot,
    generation_client: SolutionModelClient,
    quality_client: QualityModelClient,
    revision_client: RevisionModelClient,
    max_revisions: int = MAX_SOLUTION_REVISIONS,
) -> VerifiedSolutionResult:
    if not 0 <= max_revisions <= MAX_SOLUTION_REVISIONS:
        raise ValueError(f"max_revisions must be between 0 and {MAX_SOLUTION_REVISIONS}")

    solution = await generate_solution(context, retrieval_snapshot, generation_client)
    attempts: list[QualityAttempt] = []

    for attempt_number in range(1, max_revisions + 2):
        quality_report = await quality_check_solution(
            context,
            retrieval_snapshot,
            solution,
            quality_client,
        )
        attempts.append(
            QualityAttempt(
                attempt=attempt_number,
                solution=solution,
                quality_report=quality_report,
            )
        )
        if quality_report.passed or attempt_number > max_revisions:
            return VerifiedSolutionResult(
                solution=solution,
                quality_report=quality_report,
                attempts=attempts,
                revision_count=attempt_number - 1,
                passed=quality_report.passed,
            )

        solution = await revise_solution(
            context,
            retrieval_snapshot,
            solution,
            quality_report,
            revision_client,
        )

    raise RuntimeError("solution quality loop ended unexpectedly")
