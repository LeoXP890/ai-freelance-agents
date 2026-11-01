"""Ciclo de vida dos gates de certificação (GOV-006; SDD §3, §25.2).

NOT_STARTED → COLLECTING_EVIDENCE → IN_REVIEW → APPROVED | APPROVED_WITH_RESTRICTIONS | REJECTED; aprovado → EXPIRED
(validade vencida) ou revogado. A aprovação exige: evidências registradas, decisor humano com MFA (owner), escopo,
validade futura, versão de política e hash das evidências. O sistema registra a decisão; não a toma.
Perder aprovação emite `go_live.revoked`: o GoLivePolicy volta a negar imediatamente.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.canonical import sha256_of
from aicommerce.governance import audit
from aicommerce.models import CertificationGate, GateEvidence
from aicommerce.office import events

EVIDENCE_KINDS = {
    "DOCUMENTO",
    "PARECER",
    "ACEITE_RISCO",
    "RELATORIO_TESTE",
    "CONFIRMACAO_CONTA",
    "CONTRATO",
    "CERTIFICADO",
    "OUTRO",
}
APPROVED = ("APPROVED", "APPROVED_WITH_RESTRICTIONS")
SCOPE_KEYS = {"channels", "ufs", "fulfillment_modes", "supplier_ids", "product_ids", "restrictions"}


class GateError(Exception):
    http = 409


def _gate(db: Session, code: str, lock: bool = True) -> CertificationGate:
    g = db.get(CertificationGate, code, with_for_update=lock)
    if g is None:
        e = GateError("gate inexistente")
        e.http = 404
        raise e
    return g


def evidence_hash(db: Session, code: str) -> str | None:
    hashes = sorted(db.execute(select(GateEvidence.sha256).where(GateEvidence.gate_code == code)).scalars())
    return sha256_of(hashes) if hashes else None


def add_evidence(
    db: Session, code: str, *, kind: str, reference: str, sha256: str | None, notes: str, actor: str
) -> GateEvidence:
    if kind not in EVIDENCE_KINDS:
        raise GateError(f"tipo inválido; use {sorted(EVIDENCE_KINDS)}")
    g = _gate(db, code)
    if g.state in APPROVED:
        raise GateError("gate aprovado: revogue antes de alterar a base de evidências")
    ev = GateEvidence(
        gate_code=code,
        kind=kind,
        reference=reference,
        sha256=sha256 or sha256_of(reference),
        notes=notes,
        submitted_by=actor,
    )
    db.add(ev)
    if g.state in ("NOT_STARTED", "REJECTED", "EXPIRED"):
        g.state = "COLLECTING_EVIDENCE"
    g.updated_at = dt.datetime.now(dt.UTC)
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="gate.evidence.added",
        subject_type="gate",
        subject_id=code,
        payload={"kind": kind, "sha256": ev.sha256},
    )
    return ev


def submit(db: Session, code: str, *, actor: str) -> CertificationGate:
    g = _gate(db, code)
    if g.state != "COLLECTING_EVIDENCE":
        raise GateError(f"gate em {g.state}; só é possível enviar para revisão a partir de COLLECTING_EVIDENCE")
    if evidence_hash(db, code) is None:
        raise GateError("sem evidências registradas")
    g.state = "IN_REVIEW"
    audit.record(db, actor_type="user", actor_id=actor, action="gate.submitted", subject_type="gate", subject_id=code)
    return g


def decide(
    db: Session,
    code: str,
    *,
    decision: str,
    scope: dict[str, Any],
    valid_until: dt.datetime | None,
    note: str,
    actor: str,
    mfa: bool,
    policy_version: str,
) -> CertificationGate:
    g = _gate(db, code)
    if not mfa:
        raise GateError("decisão de gate exige sessão com MFA")
    if g.state != "IN_REVIEW":
        raise GateError(f"gate em {g.state}; precisa estar IN_REVIEW")
    if decision not in (*APPROVED, "REJECTED"):
        raise GateError("decisão inválida")
    if len(note.strip()) < 10:
        raise GateError("justificativa obrigatória (mín. 10 caracteres)")
    unknown = set(scope) - SCOPE_KEYS
    if unknown:
        raise GateError(f"chaves de escopo inválidas: {sorted(unknown)}")
    from aicommerce.governance import seller as _seller

    seller_type = _seller.get_profile(db).person_type
    if decision in APPROVED and code in _seller.PROFILE_SENSITIVE_GATES:
        # a aprovação vale só para o perfil em vigor (o sistema registra; mudar o perfil derruba a aprovação)
        scope = {
            **scope,
            "seller_types": [seller_type],
            "profile_fingerprint": _seller.context(db).fingerprints.get(code),  # mudança material → nova revisão
        }
        if seller_type == _seller.SellerType.PERSON.value and code in _seller.PERSON_OPINION_GATES:
            kinds = set(db.execute(select(GateEvidence.kind).where(GateEvidence.gate_code == code)).scalars())
            if "PARECER" not in kinds:
                # Sem parecer, só com aceite de risco escrito pelo proprietário (ACEITE_RISCO) e aprovação COM
                # restrições: a homologação fica marcada como risco assumido, nunca como comprovação.
                if "ACEITE_RISCO" not in kinds or decision != "APPROVED_WITH_RESTRICTIONS":
                    raise GateError(
                        "Pessoa Física: aprovar este gate exige parecer de profissional habilitado (PARECER) ou "
                        "aceite de risco do proprietário (ACEITE_RISCO) com aprovação com restrições"
                    )
                given = scope.get("restrictions") or []
                scope["restrictions"] = [
                    *([given] if isinstance(given, str) else given),
                    "aceite de risco do proprietário, sem parecer profissional",
                ]
    if decision in APPROVED:
        now = dt.datetime.now(dt.UTC)
        if valid_until is None or valid_until <= now:
            raise GateError("aprovação exige validade futura (valid_until)")
        if decision == "APPROVED_WITH_RESTRICTIONS" and not scope.get("restrictions"):
            raise GateError("aprovação com restrições exige descrever as restrições")
        g.evidence_sha256, g.approved_by, g.valid_until = evidence_hash(db, code), actor, valid_until
        g.policy_version, g.scope = policy_version, scope
    else:
        g.approved_by, g.valid_until, g.evidence_sha256 = None, None, None
    g.state, g.updated_at = decision, dt.datetime.now(dt.UTC)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="gate.decided",
        subject_type="gate",
        subject_id=code,
        payload={
            "decision": decision,
            "scope": scope,
            "valid_until": valid_until.isoformat() if valid_until else None,
            "evidence_sha256": g.evidence_sha256,
            "note": note,
        },
    )
    return g


def _lose_approval(db: Session, g: CertificationGate, *, state: str, actor: str, reason: str) -> None:
    was = g.state
    g.state, g.updated_at = state, dt.datetime.now(dt.UTC)
    audit.record(
        db,
        actor_type="user" if actor != "system" else "system",
        actor_id=actor,
        action="gate.revoked",
        subject_type="gate",
        subject_id=g.code,
        payload={"from": was, "to": state, "reason": reason},
    )
    events.emit(
        db, "go_live.revoked", agent_id="clara", payload={"gate": g.code, "from": was, "to": state, "reason": reason}
    )


def revoke(db: Session, code: str, *, actor: str, mfa: bool, reason: str) -> CertificationGate:
    g = _gate(db, code)
    if not mfa:
        raise GateError("revogação exige sessão com MFA")
    if g.state not in APPROVED:
        raise GateError(f"gate em {g.state} não está aprovado")
    _lose_approval(db, g, state="REJECTED", actor=actor, reason=reason)
    return g


def expire_due(db: Session, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.UTC)
    due = db.execute(
        select(CertificationGate)
        .where(CertificationGate.state.in_(APPROVED), CertificationGate.valid_until <= now)
        .with_for_update()
    ).scalars()
    n = 0
    for g in due:
        _lose_approval(db, g, state="EXPIRED", actor="system", reason="validade vencida")
        n += 1
    return n
