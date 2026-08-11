import inspect
from pathlib import Path

import yaml

from app.contracts.ai import AIEngine
from app.main import app


def test_frozen_ai_engine_signatures_are_unchanged() -> None:
    methods = [
        "extract_profile",
        "extract_experience",
        "extract_capabilities",
        "extract_search_intent",
        "generate_solution",
    ]
    assert [name for name in AIEngine.__dict__ if not name.startswith("_")] == methods
    assert list(inspect.signature(AIEngine.generate_solution).parameters) == [
        "self",
        "context",
        "retrieval_snapshot",
    ]


def test_v2_incremental_openapi_is_valid_and_matches_routes() -> None:
    contract_path = Path("docs/openapi-v2-incremental.yaml")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    assert contract["openapi"] == "3.1.0"
    generated_paths = app.openapi()["paths"]
    for path, operations in contract["paths"].items():
        mounted = generated_paths[f"/api/v1{path}"]
        for method in operations:
            assert method in mounted


def test_v2_contract_does_not_replace_frozen_contracts() -> None:
    assert Path("openapi.yaml").is_file()
    assert Path("app/contracts/ai.py").is_file()
    contract = yaml.safe_load(Path("docs/openapi-v2-incremental.yaml").read_text(encoding="utf-8"))
    assert contract["info"]["version"] == "2.0.0"
    assert "/solution-runs" not in contract["paths"]


def test_v2_contract_freezes_raw_artifact_and_document_locations() -> None:
    contract = yaml.safe_load(Path("docs/openapi-v2-incremental.yaml").read_text(encoding="utf-8"))
    schemas = contract["components"]["schemas"]

    assert schemas["RawArtifact"]["properties"]["content_sha256"]["pattern"]
    assert "partial" in schemas["RawArtifactStatus"]["enum"]
    assert schemas["DocumentLocation"]["discriminator"]["propertyName"] == "kind"
    assert set(schemas["DocumentLocation"]["discriminator"]["mapping"]) == {
        "pdf_page",
        "docx_paragraph",
        "xlsx_cell",
        "pptx_shape",
        "plain_text",
    }
    requirement = schemas["TenderRequirement"]
    assert requirement["properties"]["source_location"] == {
        "$ref": "#/components/schemas/DocumentLocation"
    }
    assert contract["components"]["parameters"]["IdempotencyKey"]["schema"]["minLength"] == 16


def test_ai_workbench_frontend_uses_v2_backend_endpoints() -> None:
    frontend = Path("static/index.html").read_text(encoding="utf-8")
    assert 'apiFetch("/runtime/tasks?page=1&page_size=100")' in frontend
    assert 'apiFetch("/model-connections")' in frontend
    assert "sessions.filter(x=>x.result?.runId)" not in frontend
