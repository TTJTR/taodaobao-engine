from pathlib import Path

import yaml


def test_style_template_candidate_routes_are_idempotent_and_uuid_typed() -> None:
    contract = yaml.safe_load(
        Path("docs/openapi-presentation-v1.1-incremental.yaml").read_text(encoding="utf-8")
    )
    paths = contract["paths"]
    base = "/api/v1/style-profiles/{style_profile_id}/template-candidates"
    regenerate = f"{base}/regenerate-preview"
    confirm = f"{base}/{{candidate_id}}/confirm"

    assert "get" in paths[base]
    for path in (regenerate, confirm):
        parameters = [
            item.get("$ref", item.get("name")) for item in paths[path]["post"]["parameters"]
        ]
        assert "#/components/parameters/IdempotencyKey" in parameters
    candidate_parameter = next(
        item
        for item in paths[confirm]["post"]["parameters"]
        if item.get("name") == "candidate_id"
    )
    assert candidate_parameter["schema"]["format"] == "uuid"
