from pathlib import Path

from scripts.configure_feishu_runtime import (
    generate_fernet_key,
    parse_env,
    render_env,
    validate_live_config,
    write_atomically,
)


def test_render_env_preserves_unrelated_lines_and_updates_existing_values() -> None:
    original = "# retained\nAPP_ENV=production\nAPP_FEISHU_MODE=mock\n"
    rendered = render_env(
        original,
        {"APP_FEISHU_MODE": "live", "APP_PUBLIC_BASE_URL": "http://8.218.59.190"},
    )

    assert "# retained" in rendered
    assert "APP_ENV=production" in rendered
    assert "APP_FEISHU_MODE=live" in rendered
    assert "APP_FEISHU_MODE=mock" not in rendered
    assert "APP_PUBLIC_BASE_URL=http://8.218.59.190" in rendered


def test_generated_fernet_key_has_expected_shape() -> None:
    key = generate_fernet_key()

    assert len(key) == 44
    assert key.endswith("=")


def test_validate_live_config_reports_missing_and_malformed_values() -> None:
    missing = validate_live_config(
        {"APP_PUBLIC_BASE_URL": "8.218.59.190", "APP_FEISHU_APP_ID": "bad-id"}
    )

    assert "APP_FEISHU_APP_SECRET" in missing
    assert "APP_PUBLIC_BASE_URL (must start with http:// or https://)" in missing
    assert "APP_FEISHU_APP_ID (expected cli_ prefix)" in missing


def test_atomic_write_creates_private_backup(tmp_path: Path) -> None:
    env_path = tmp_path / "runtime.env"
    env_path.write_text("APP_FEISHU_MODE=mock\n", encoding="utf-8")

    backup = write_atomically(env_path, "APP_FEISHU_MODE=live\n")

    assert backup is not None
    assert backup.read_text(encoding="utf-8") == "APP_FEISHU_MODE=mock\n"
    assert env_path.read_text(encoding="utf-8") == "APP_FEISHU_MODE=live\n"
    assert parse_env(env_path.read_text(encoding="utf-8"))["APP_FEISHU_MODE"] == "live"
