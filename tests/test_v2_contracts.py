import inspect
from pathlib import Path

import yaml

from app.ai.schemas import CustomerProfileDraft, SolutionContext
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


def test_solution_context_accepts_separate_external_intelligence_context() -> None:
    external_context = {
        "boundary": "external background only; never internal capability evidence",
        "intelligence_snapshot": {"id": "snapshot-1", "model_context": {"facts": []}},
    }

    context = SolutionContext(
        customer_profile=CustomerProfileDraft(
            customer_name="测试客户",
            profile_summary="用于验证外部情报边界的测试画像。",
            source_ids=["profile-source-1"],
        ),
        current_requirement="形成一份有可信边界的方案",
        external_context=external_context,
    )

    assert context.external_context == external_context


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


def test_intelligence_enrichment_write_requires_idempotency_key() -> None:
    contract = yaml.safe_load(Path("docs/openapi-v2-incremental.yaml").read_text(encoding="utf-8"))
    operation = contract["paths"]["/intelligence/raw-artifacts/{artifact_id}/enrich"]["post"]
    assert {parameter.get("$ref") for parameter in operation["parameters"]} >= {
        "#/components/parameters/IdempotencyKey"
    }


def test_tender_breakdown_and_review_contracts_enforce_trust_gate() -> None:
    contract = yaml.safe_load(Path("docs/openapi-v2-incremental.yaml").read_text(encoding="utf-8"))
    paths = contract["paths"]
    schemas = contract["components"]["schemas"]
    for path, method in (
        ("/tenders/{tender_id}/requirements/breakdown", "post"),
        ("/response-matrices/{matrix_id}/items/{item_id}/review", "post"),
    ):
        refs = {item.get("$ref") for item in paths[path][method]["parameters"]}
        assert "#/components/parameters/IdempotencyKey" in refs
    assert schemas["ReviewResponseItemRequest"]["properties"]["action"]["enum"] == [
        "approve",
        "edit_and_approve",
        "reject",
        "needs_evidence",
    ]
    assert "expected_version" in schemas["ReviewResponseItemRequest"]["required"]
    assert schemas["ResponseMatrixItem"]["properties"]["ai_draft"] == {"type": "string"}


def test_tender_requirement_correction_contracts_are_idempotent_and_versioned() -> None:
    contract = yaml.safe_load(Path("docs/openapi-v2-incremental.yaml").read_text(encoding="utf-8"))
    paths = contract["paths"]
    operations = (
        ("/tenders/{tender_id}/requirements/{requirement_id}", "patch"),
        ("/tenders/{tender_id}/requirements/{requirement_id}", "delete"),
        ("/tenders/{tender_id}/requirements/{requirement_id}/confirm", "post"),
        ("/tenders/{tender_id}/requirements/merge", "post"),
        ("/tenders/{tender_id}/requirements/{requirement_id}/split", "post"),
    )
    for path, method in operations:
        refs = {item.get("$ref") for item in paths[path][method]["parameters"]}
        assert "#/components/parameters/IdempotencyKey" in refs
    schemas = contract["components"]["schemas"]
    assert "expected_version" in schemas["UpdateTenderRequirementRequest"]["required"]
    assert "expected_version" in schemas["SplitTenderRequirementRequest"]["required"]
    assert schemas["MergeTenderRequirementsRequest"]["properties"]["requirement_ids"][
        "minItems"
    ] == 2


def test_ai_workbench_frontend_uses_v2_backend_endpoints() -> None:
    frontend = Path("static/index.html").read_text(encoding="utf-8")
    assert 'apiFetch("/runtime/tasks?page=1&page_size=100")' in frontend
    assert 'apiFetch("/model-connections")' in frontend
    assert "sessions.filter(x=>x.result?.runId)" not in frontend
