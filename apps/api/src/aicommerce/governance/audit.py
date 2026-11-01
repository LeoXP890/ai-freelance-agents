"""Trilha de auditoria append-only com cadeia de hash.

Cada evento referencia o hash anterior; a serialização é feita com advisory lock transacional, então
inserções concorrentes não bifurcam a cadeia. `verify_chain` detecta adulteração ou lacunas.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aicommerce.canonical import sha256_of
from aicommerce.config import get_settings
from aicommerce.models import AuditEvent

GENESIS = "0" * 64
_SECRET_KEYS = re.compile(r"(password|secret|token|totp|authorization|api_key|cpf|card)", re.IGNORECASE)


def redact(value: Any) -> Any:
    """Remove segredos/PII óbvios antes de persistir (logs sem segredos — SDD §5)."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _event_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return sha256_of({"prev": prev_hash, **body})


def record(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('aic_audit_chain'))"))
    last = db.execute(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1)).scalar()
    prev = last or GENESIS
    occurred = dt.datetime.now(dt.UTC)
    body = {
        "occurred_at": occurred.isoformat(),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": action,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "mode": get_settings().mode.value,
        "request_id": request_id,
        "payload": redact(payload or {}),
    }
    event = AuditEvent(
        occurred_at=occurred,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        mode=body["mode"],
        request_id=request_id,
        payload=body["payload"],
        prev_hash=prev,
        hash=_event_hash(prev, body),
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(db: Session) -> tuple[bool, int | None]:
    """Retorna (íntegro, id_do_primeiro_evento_inválido)."""
    prev = GENESIS
    for ev in db.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars():
        body = {
            "occurred_at": ev.occurred_at.astimezone(dt.UTC).isoformat(),
            "actor_type": ev.actor_type,
            "actor_id": ev.actor_id,
            "action": ev.action,
            "subject_type": ev.subject_type,
            "subject_id": ev.subject_id,
            "mode": ev.mode,
            "request_id": ev.request_id,
            "payload": ev.payload,
        }
        if ev.prev_hash != prev or ev.hash != _event_hash(prev, body):
            return False, ev.id
        prev = ev.hash
    return True, None
