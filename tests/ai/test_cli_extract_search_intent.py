from pathlib import Path

from app.ai.cli.extract_search_intent import build_parser


def test_cli_parser_reads_profile_and_requirement() -> None:
    args = build_parser().parse_args(
        [
            "profile.json",
            "--requirement",
            "减少人工图片复看",
            "--env-file",
            ".env",
        ]
    )

    assert args.profile_file == Path("profile.json")
    assert args.requirement == "减少人工图片复看"
    assert args.env_file == Path(".env")
