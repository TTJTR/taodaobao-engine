import argparse
import asyncio
from pathlib import Path

from app.ai.model_client import BailianChatClient, BailianSettings, ModelClientError
from app.ai.pipelines.extractor import extract_capabilities_from_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract capability cards with Bailian.")
    parser.add_argument("source_file", type=Path, help="UTF-8 PRD or product document")
    parser.add_argument("--source-id", required=True, help="Source id such as SRC-PRD-001")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file containing DASHSCOPE_API_KEY",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    if not args.source_file.is_file():
        raise FileNotFoundError(f"Source file does not exist: {args.source_file}")

    settings = BailianSettings.from_env(args.env_file)
    client = BailianChatClient(settings)
    capabilities = await extract_capabilities_from_text(
        args.source_file.read_text(encoding="utf-8"),
        args.source_id,
        client,
    )
    print("[" + ",\n".join(item.model_dump_json(indent=2) for item in capabilities) + "]")
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
