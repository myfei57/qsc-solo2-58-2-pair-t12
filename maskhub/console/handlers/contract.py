"""Explicit data-contract HTTP endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.console.validation import optional_text, require_identifier, require_text
from maskhub.contract.schema import ContractField
from maskhub.errors import InvalidRequest


def _parse_fields(raw_fields: object) -> tuple[ContractField, ...]:
    if not isinstance(raw_fields, list) or not raw_fields:
        raise InvalidRequest("fields must be a non-empty list")
    fields: list[ContractField] = []
    for raw in raw_fields:
        if not isinstance(raw, dict):
            raise InvalidRequest("each field must be an object")
        fields.append(
            ContractField(
                name=require_text(raw, "name"),
                data_type=require_text(raw, "data_type"),
                required=_optional_bool(raw, "required", True),
                nullable=_optional_bool(raw, "nullable", False),
            )
        )
    return tuple(fields)


def _optional_bool(payload: dict[str, object], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise InvalidRequest(f"{key} must be a boolean")
    return value


def register_contract(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_identifier(payload, "source")
    contract = runtime.contracts_service.register(source, _parse_fields(payload.get("fields")))
    return Response.json(200, contract.as_dict())


def get_contract(runtime, request: Request) -> Response:
    source = request.query_one("source", "") or ""
    return Response.json(200, runtime.contracts_service.current(source).as_dict())


def contract_versions(runtime, request: Request) -> Response:
    source = request.query_one("source", "") or ""
    versions = runtime.contracts_service.history(source)
    return Response.json(200, {"count": len(versions), "versions": [item.as_dict() for item in versions]})


def ingest_batch(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_identifier(payload, "source")
    batch_id = require_identifier(payload, "batch_id")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise InvalidRequest("rows must be a non-empty list")
    result = runtime.contracts_service.ingest(source, batch_id, rows)
    status = 202 if result.batch.state == "held" else 200
    return Response.json(status, result.as_dict())


def approve_contract(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_identifier(payload, "source")
    version = _require_version(payload)
    result = runtime.contracts_service.approve(source, version, optional_text(payload, "reason"))
    return Response.json(200, result)


def reject_contract(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_identifier(payload, "source")
    version = _require_version(payload)
    result = runtime.contracts_service.reject(source, version, optional_text(payload, "reason"))
    return Response.json(200, result)


def list_batches(runtime, request: Request) -> Response:
    source = request.query_one("source", "") or ""
    batches = runtime.contracts_service.batches_for(source)
    return Response.json(
        200,
        {"count": len(batches), "batches": [_without_raw(item.as_dict()) for item in batches]},
    )


def get_batch(runtime, request: Request) -> Response:
    batch_id = request.query_one("batch_id", "") or ""
    include_raw = request.query_one("raw") == "1"
    document = runtime.contracts_service.batch(batch_id).as_dict()
    return Response.json(200, document if include_raw else _without_raw(document))


def _require_version(payload: dict[str, object]) -> int:
    value = payload.get("version")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidRequest("version must be a positive integer")
    return value


def _without_raw(document: dict[str, object]) -> dict[str, object]:
    safe = dict(document)
    safe.pop("raw_records", None)
    return safe
