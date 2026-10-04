"""Contract-aware ingestion endpoints."""

from __future__ import annotations

from typing import Any

from maskhub.console.http import Request, Response
from maskhub.console.validation import optional_text, require_text
from maskhub.contract.column import ContractColumn
from maskhub.errors import InvalidRequest


def _parse_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise InvalidRequest("records must be a non-empty list")
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise InvalidRequest("each record must be an object")
        records.append(item)
    return records


def _parse_columns(payload: Any) -> tuple[ContractColumn, ...]:
    if not isinstance(payload, list) or not payload:
        raise InvalidRequest("columns must be a non-empty list")
    columns = tuple(ContractColumn.parse(item) for item in payload)
    names = [column.name for column in columns]
    if len(names) != len(set(names)):
        raise InvalidRequest("columns must be unique")
    return columns


def collect(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = require_text(payload, "source")
    records = _parse_records(payload.get("records"))
    explicit_columns = _parse_columns(payload["columns"]) if "columns" in payload else None
    actor = optional_text(payload, "actor", "gateway")
    idempotency_key = payload.get("idempotency_key")
    if idempotency_key is not None and not isinstance(idempotency_key, str):
        raise InvalidRequest("idempotency_key must be a string")
    batch = runtime.collection.collect(source, records, explicit_columns, actor, idempotency_key)
    body = batch.as_dict(include_records=False)
    return Response.json(202 if batch.status == "held" else 200, body)


def get_batch(runtime, request: Request) -> Response:
    identifier = request.query_one("batch", "") or ""
    batch = runtime.batches.get(identifier)
    return Response.json(200, batch.as_dict(include_records=False))


def list_batches(runtime, request: Request) -> Response:
    source = request.query_one("source")
    status = request.query_one("status")
    batches = runtime.batches.for_source(source) if source else runtime.batches.all()
    if status:
        batches = [batch for batch in batches if batch.status == status]
    return Response.json(
        200,
        {"count": len(batches), "batches": [batch.as_dict(include_records=False) for batch in batches]},
    )


def replay_batch(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "batch")
    actor = optional_text(payload, "actor", "gateway")
    batch = runtime.collection.replay(identifier, actor)
    return Response.json(200, batch.as_dict(include_records=False))
