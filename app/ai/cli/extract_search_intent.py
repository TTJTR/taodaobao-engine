import argparse
import asyncio
from pathlib import Path

from app.ai.model_client import BailianChatClient, BailianSettings, ModelClientError
from app.ai.pipelines.search_intent import extract_search_intent
from app.ai.schemas import CustomerProfileDraft, SolutionContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract a search intent with Bailian.")
    parser.add_argument("profile_file", type=Path, help="UTF-8 customer profile JSON file")
    parser.add_argument("--requirement", required=True, help="The customer's current requirement")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file containing DASHSCOPE_API_KEY",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    if not args.profile_file.is_file():
        raise FileNotFoundError(f"Profile file does not exist: {args.profile_file}")

    profile = CustomerProfileDraft.model_validate_json(
        args.profile_file.read_text(encoding="utf-8")
    )
    context = SolutionContext(
        customer_profile=profile,
        current_requirement=args.requirement,
    )
    settings = BailianSettings.from_env(args.env_file)
    client = BailianChatClient(settings)
    intent = await extract_search_intent(context, client)
    print(intent.model_dump_json(indent=2))
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
