from pathlib import Path

from app.ai.cli.extract_experience import build_parser


def test_cli_parser_reads_source_id_and_file() -> None:
    args = build_parser().parse_args(
        ["review.md", "--source-id", "SRC-EXP-001", "--env-file", ".env"]
    )

    assert args.source_file == Path("review.md")
    assert args.source_id == "SRC-EXP-001"
    assert args.env_file == Path(".env")
