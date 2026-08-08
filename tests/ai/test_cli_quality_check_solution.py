from pathlib import Path

from app.ai.cli.quality_check_solution import build_parser


def test_cli_parser_reads_all_quality_check_files() -> None:
    args = build_parser().parse_args(
        [
            "context.json",
            "snapshot.json",
            "solution.json",
            "--env-file",
            ".env",
        ]
    )

    assert args.context_file == Path("context.json")
    assert args.retrieval_snapshot_file == Path("snapshot.json")
    assert args.solution_file == Path("solution.json")
    assert args.env_file == Path(".env")
