import argparse
import asyncio
from pathlib import Path

from app.ai.engine import BailianAIEngine, MockAIEngine
from app.ai.evaluation import load_evaluation_suite, run_evaluation_suite
from app.ai.model_client import BailianChatClient, BailianSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 AI 业务回归评测集")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/business_cases.json"),
        help="评测集 JSON 路径",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/latest_report.json"),
        help="评测报告输出路径",
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "bailian"),
        default="mock",
        help="使用离线 Mock 或真实百炼运行评测",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="真实百炼模式的环境变量文件",
    )
    return parser


async def run(args: argparse.Namespace) -> None:
    suite, cases = load_evaluation_suite(args.cases)
    if args.mode == "bailian":
        settings = BailianSettings.from_env(args.env_file)
        client = BailianChatClient(settings)
        engine = BailianAIEngine(client)
        model_version = client.model_version
    else:
        engine = MockAIEngine()
        model_version = "mock-v1"
    report = await run_evaluation_suite(
        suite,
        cases,
        engine,
        model_version=model_version,
        prompt_version="solution-v1",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(report.summary.model_dump_json(indent=2))


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
