"""Explicit contract endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.console.validation import optional_text, require_text
from maskhub.contract.column import ContractColumn
from maskhub.errors import InvalidRequest


def _parse_columns(payload: object) -> tuple[ContractColumn, ...]:
    if not isinstance(payload, list) or not payload:
        raise InvalidRequest("columns must be a non-empty list")
    columns = tuple(ContractColumn.parse(item) for item in payload)
    names = [column.name for column in columns]
    if len(names) != len(set(names)):
        raise InvalidRequest("columns must be unique")
    return columns


def register_contract(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_text(payload, "source")
    runtime.sources.get(source)
    columns = _parse_columns(payload.get("columns"))
    actor = optional_text(payload, "created_by", "gateway")
    reason = optional_text(payload, "reason", "initial registration")
    version = runtime.contracts.register(source, columns, actor, reason)
    runtime.journal.append("contract.register", source, version.version)
    return Response.json(200, version.as_dict())


def current_contract(runtime, request: Request) -> Response:
    source = request.query_one("source", "") or ""
    pending = runtime.contracts.pending(source)
    return Response.json(
        200,
        {
            "current": runtime.contracts.current(source).as_dict(),
            "pending": pending.as_dict() if pending else None,
        },
    )


def contract_history(runtime, request: Request) -> Response:
    source = request.query_one("source", "") or ""
    versions = runtime.contracts.history(source)
    return Response.json(
        200,
        {"source": source, "count": len(versions), "versions": [item.as_dict() for item in versions]},
    )


def approve_contract(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_text(payload, "source")
    actor = optional_text(payload, "approved_by", "gateway")
    reason = optional_text(payload, "reason", "destructive change approved")
    columns = _parse_columns(payload["columns"]) if "columns" in payload else None
    result = runtime.collection.approve(source, actor, columns, reason)
    return Response.json(200, result)


def reject_contract(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_text(payload, "source")
    actor = optional_text(payload, "rejected_by", "gateway")
    reason = optional_text(payload, "reason", "destructive change rejected")
    return Response.json(200, runtime.collection.reject(source, actor, reason))
