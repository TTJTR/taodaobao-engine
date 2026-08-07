from pathlib import Path

from app.ai.cli.generate_verified_solution import build_parser


def test_cli_parser_reads_inputs_and_revision_limit() -> None:
    args = build_parser().parse_args(
        [
            "context.json",
            "snapshot.json",
            "--max-revisions",
            "1",
            "--output",
            "result.json",
            "--env-file",
            ".env",
        ]
    )

    assert args.context_file == Path("context.json")
    assert args.retrieval_snapshot_file == Path("snapshot.json")
    assert args.max_revisions == 1
    assert args.output == Path("result.json")
    assert args.env_file == Path(".env")
