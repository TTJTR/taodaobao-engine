import argparse
import asyncio
from pathlib import Path

from app.ai.model_client import BailianChatClient, BailianSettings, ModelClientError
from app.ai.pipelines.workflow import generate_verified_solution
from app.ai.schemas import RetrievalSnapshot, SolutionContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate, quality-check, and revise a cited solution with Bailian."
    )
    parser.add_argument("context_file", type=Path, help="UTF-8 SolutionContext JSON file")
    parser.add_argument(
        "retrieval_snapshot_file",
        type=Path,
        help="UTF-8 RetrievalSnapshot JSON file",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        choices=range(0, 3),
        default=2,
        help="Maximum quality-driven revisions, from 0 to 2",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Save the full JSON result to this UTF-8 file",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file containing DASHSCOPE_API_KEY",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    for path in (args.context_file, args.retrieval_snapshot_file):
        if not path.is_file():
            raise FileNotFoundError(f"Input file does not exist: {path}")

    context = SolutionContext.model_validate_json(args.context_file.read_text(encoding="utf-8"))
    snapshot = RetrievalSnapshot.model_validate_json(
        args.retrieval_snapshot_file.read_text(encoding="utf-8")
    )
    settings = BailianSettings.from_env(args.env_file)
    client = BailianChatClient(settings)
    result = await generate_verified_solution(
        context,
        snapshot,
        generation_client=client,
        quality_client=client,
        revision_client=client,
        max_revisions=args.max_revisions,
    )
    result_json = result.model_dump_json(indent=2)
    if args.output is None:
        print(result_json)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result_json + "\n", encoding="utf-8")
        status = "通过" if result.passed else "未通过，需人工检查"
        print(f"完整结果已保存：{args.output}")
        print(f"质检结果：{status}")
        print(f"实际重写次数：{result.revision_count}")
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
