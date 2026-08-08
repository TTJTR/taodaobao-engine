from pathlib import Path

from app.ai.cli.extract_profile import build_parser


def test_cli_parser_accepts_multiple_source_ids() -> None:
    args = build_parser().parse_args(
        [
            "meeting.md",
            "--source-id",
            "SRC-CUST-001",
            "--source-id",
            "SRC-CUST-002",
        ]
    )

    assert args.source_file == Path("meeting.md")
    assert args.source_ids == ["SRC-CUST-001", "SRC-CUST-002"]
