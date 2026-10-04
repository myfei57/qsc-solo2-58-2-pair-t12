"""Versioned data-source contract records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from maskhub.contract.schema import ContractField


class ContractState(str, Enum):
    ACTIVE = "active"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Contract:
    source: str
    version: int
    fields: tuple[ContractField, ...]
    state: str
    registered_at: str = ""
    decided_at: str = ""
    reason: str = ""
    based_on_version: int | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("contract source is required")
        if self.version < 1:
            raise ValueError("contract version must be positive")
        if not self.fields:
            raise ValueError("contract must declare at least one field")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("contract fields must be unique")
        if self.state not in {item.value for item in ContractState}:
            raise ValueError(f"unknown contract state {self.state!r}")
        if self.based_on_version is not None and self.based_on_version < 1:
            raise ValueError("based_on_version must be positive")

    @property
    def state_enum(self) -> ContractState:
        return ContractState(self.state)

    def field(self, name: str) -> ContractField | None:
        for item in self.fields:
            if item.name == name:
                return item
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "version": self.version,
            "fields": [field.as_dict() for field in self.fields],
            "state": self.state,
            "registered_at": self.registered_at,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "based_on_version": self.based_on_version,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Contract | None":
        if not isinstance(payload, dict):
            return None
        source = payload.get("source")
        version = payload.get("version")
        raw_fields = payload.get("fields")
        state = payload.get("state")
        registered_at = payload.get("registered_at", "")
        decided_at = payload.get("decided_at", "")
        reason = payload.get("reason", "")
        based_on = payload.get("based_on_version")
        if not isinstance(source, str) or not isinstance(version, int) or isinstance(version, bool):
            return None
        if not isinstance(state, str) or not isinstance(raw_fields, list):
            return None
        if not all(isinstance(item, str) for item in (registered_at, decided_at, reason)):
            return None
        if based_on is not None and (not isinstance(based_on, int) or isinstance(based_on, bool)):
            return None
        fields = tuple(
            item for item in (ContractField.from_dict(raw) for raw in raw_fields) if item is not None
        )
        if len(fields) != len(raw_fields):
            return None
        try:
            return cls(
                source=source,
                version=version,
                fields=fields,
                state=state,
                registered_at=registered_at,
                decided_at=decided_at,
                reason=reason,
                based_on_version=based_on,
            )
        except ValueError:
            return None
