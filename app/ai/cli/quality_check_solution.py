import argparse
import asyncio
from pathlib import Path

from app.ai.model_client import BailianChatClient, BailianSettings, ModelClientError
from app.ai.pipelines.quality import quality_check_solution
from app.ai.schemas import RetrievalSnapshot, Solution, SolutionContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quality-check a cited solution with Bailian.")
    parser.add_argument("context_file", type=Path, help="UTF-8 SolutionContext JSON file")
    parser.add_argument(
        "retrieval_snapshot_file",
        type=Path,
        help="UTF-8 RetrievalSnapshot JSON file",
    )
    parser.add_argument("solution_file", type=Path, help="UTF-8 Solution JSON file")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file containing DASHSCOPE_API_KEY",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    for path in (args.context_file, args.retrieval_snapshot_file, args.solution_file):
        if not path.is_file():
            raise FileNotFoundError(f"Input file does not exist: {path}")

    context = SolutionContext.model_validate_json(args.context_file.read_text(encoding="utf-8"))
    snapshot = RetrievalSnapshot.model_validate_json(
        args.retrieval_snapshot_file.read_text(encoding="utf-8")
    )
    solution = Solution.model_validate_json(args.solution_file.read_text(encoding="utf-8"))
    settings = BailianSettings.from_env(args.env_file)
    client = BailianChatClient(settings)
    report = await quality_check_solution(context, snapshot, solution, client)
    print(report.model_dump_json(indent=2))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except ModelClientError as exc:
        print(f"百炼调用失败：{exc}")
        return 1
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"输入或配置错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
