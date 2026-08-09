from typing import Any

from fastapi import Request


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def success_response(request: Request, data: Any) -> dict[str, Any]:
    return {"request_id": get_request_id(request), "data": data}


def error_response(
    request: Request,
    *,
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_details = dict(details or {})
    # Preserve the frozen V0.5 envelope while making V1 failures traceable.
    normalized_details.setdefault("trace_id", None)
    normalized_details.setdefault("stage", None)
    normalized_details.setdefault("reason_code", code)
    return {
        "request_id": get_request_id(request),
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": normalized_details,
        },
    }
