"""QA-007: sem credenciais/gates, LIVE nunca chega a um adaptador — nem com o job já na fila.

Simula o pior caso (bug ou adulteração que coloca um job autorizado em modo LIVE) e prova cada barreira do executor
isoladamente: reavaliação da política, capacidade do adaptador e o próprio cliente real (que não envia nada).
"""

import socket

import pytest
from sqlalchemy import select, update

from aicommerce.db import session_scope
from aicommerce.governance import executor
from aicommerce.models import ApprovalProposal, ExecutionJob, ExternalActionAttempt
from aicommerce.providers import marketplaces, mock, woocommerce
from aicommerce.providers.contracts import CommandEnvelope, ErrorKind, ResultStatus
from tests.test_executor import approved

PROVIDERS = [
    mock.MockSupplierProvider,
    mock.MockAdsProvider,
    woocommerce.MockWooCommerceProvider,
    woocommerce.WooCommerceRestProvider,
    marketplaces.MockMarketplaceProvider,
    marketplaces.MLRestProvider,
]


@pytest.fixture
def spy(monkeypatch):
    calls: list[str] = []
    for cls in PROVIDERS:
        orig = cls.execute

        def wrapped(self, cmd, _orig=orig):
            calls.append(cmd.idempotency_key)
            return _orig(self, cmd)

        monkeypatch.setattr(cls, "execute", wrapped)
    return calls


def tamper_to_live(prop_id: str) -> str:
    with session_scope() as db:
        db.execute(update(ApprovalProposal).where(ApprovalProposal.id == prop_id).values(mode="LIVE"))
        db.execute(update(ExecutionJob).where(ExecutionJob.proposal_id == prop_id).values(mode="LIVE"))
        return db.execute(select(ExecutionJob.idempotency_key).where(ExecutionJob.proposal_id == prop_id)).scalar_one()


def run_job(prop_id: str) -> ExecutionJob:
    for _ in range(50):
        with session_scope() as db:
            job = db.execute(select(ExecutionJob).where(ExecutionJob.proposal_id == prop_id)).scalar_one()
            if job.status not in ("QUEUED", "RETRY"):
                return job
        if not executor.execute_next():
            break
    with session_scope() as db:
        return db.execute(select(ExecutionJob).where(ExecutionJob.proposal_id == prop_id)).scalar_one()


def attempts(job_id) -> int:
    with session_scope() as db:
        return len(db.execute(select(ExternalActionAttempt).where(ExternalActionAttempt.job_id == job_id)).all())


def test_live_job_blocked_by_policy_reevaluation(client, auth, spy):
    prop = approved(client, auth("operator"), auth("owner"))
    key = tamper_to_live(prop["id"])
    job = run_job(prop["id"])
    assert job.status == "FAILED" and job.error_kind == ErrorKind.POLICY_DENIED.value
    assert key not in spy and attempts(job.id) == 0


def test_live_job_blocked_by_adapter_capability_even_if_policy_bypassed(client, auth, spy, monkeypatch):
    prop = approved(client, auth("operator"), auth("owner"))
    key = tamper_to_live(prop["id"])
    monkeypatch.setattr(executor, "_preflight", lambda *a, **k: None)  # simula falha total da 1ª barreira
    job = run_job(prop["id"])
    assert job.status == "FAILED" and job.error_kind == ErrorKind.UNSUPPORTED.value
    assert key not in spy and attempts(job.id) == 0


@pytest.mark.parametrize("provider", [woocommerce.WooCommerceRestProvider(), marketplaces.MLRestProvider()])
def test_real_clients_never_open_a_connection(provider, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("tentativa de conexão de rede")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    cmd = CommandEnvelope(
        request_id="r",
        idempotency_key="live-guard",
        mode="LIVE",
        capability=provider.capability("publish_listing", "LIVE"),
        approval_token_ref="g",
        expected_version=1,
        operation="publish_listing",
        payload={"name": "x"},
        attempt=1,
    )
    r = provider.execute(cmd)
    assert r.status is ResultStatus.FAILED and r.error_kind is ErrorKind.UNSUPPORTED
    assert provider.lookup("live-guard") is None


def test_control_dry_run_job_does_reach_the_mock(client, auth, spy):
    """Controle: sem adulteração, o mesmo fluxo chega ao mock — o espião funciona."""
    prop = approved(client, auth("operator"), auth("owner"))
    job = run_job(prop["id"])
    assert job.status == "SUCCEEDED" and job.idempotency_key in spy
