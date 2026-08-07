from app.main import app


def test_profile_and_asset_contract_routes_are_mounted() -> None:
    paths = app.openapi()["paths"]

    expected_operations = {
        "/api/v1/customer-profiles": {"get", "post"},
        "/api/v1/customer-profiles/{profile_id}": {"get", "patch"},
        "/api/v1/customer-profiles/{profile_id}/generate": {"post"},
        "/api/v1/customer-profiles/{profile_id}/confirm": {"post"},
        "/api/v1/experiences": {"get"},
        "/api/v1/experiences/{experience_id}": {"get", "patch"},
        "/api/v1/experiences/{experience_id}/review": {"post"},
        "/api/v1/capabilities": {"get"},
        "/api/v1/capabilities/{capability_id}": {"get", "patch"},
        "/api/v1/capabilities/{capability_id}/review": {"post"},
    }
    for path, methods in expected_operations.items():
        assert methods.issubset(paths[path])
