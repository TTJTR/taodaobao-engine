from pathlib import Path

from app.ai.cli.extract_capabilities import build_parser


def test_cli_parser_reads_source_id_and_file() -> None:
    args = build_parser().parse_args(
        ["product.md", "--source-id", "SRC-PRD-001", "--env-file", ".env"]
    )

    assert args.source_file == Path("product.md")
    assert args.source_id == "SRC-PRD-001"
    assert args.env_file == Path(".env")
