from pathlib import Path

from app.ai.cli.generate_solution import build_parser


def test_cli_parser_reads_context_and_snapshot_files() -> None:
    args = build_parser().parse_args(["context.json", "snapshot.json", "--env-file", ".env"])

    assert args.context_file == Path("context.json")
    assert args.retrieval_snapshot_file == Path("snapshot.json")
    assert args.env_file == Path(".env")
