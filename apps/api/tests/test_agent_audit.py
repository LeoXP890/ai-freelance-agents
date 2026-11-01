"""Seção 6 da finalização: os 13 agentes auditados a partir do código/banco, com níveis de validação separados.
Qualidade com modelo real nunca é declarada a partir do mock."""

from __future__ import annotations

import json

from aicommerce.ai import agent_audit, evals
from tests.test_missions import drain as drain_tasks
from tests.test_missions import start
from tests.test_orders import order, shop  # noqa: F401 — fixture: anúncio publicado pelo fluxo real


def test_all_13_agents_engineering_ready_and_mock_evaluated_but_never_production_ready(client, auth, shop):  # noqa: F811
    order(
        client, shop
    )  # pedido real (simulado) para a Eva; o anúncio/oferta do shop servem a Vera, Iris, Pixel e Dante
    start(client, auth("operator"), "avaliar luminária de mesa", steps=2)
    drain_tasks()  # missão concluída para o resumo do Atlas
    ev = evals.run()
    report = agent_audit.audit(ev)
    skipped = [t["kind"] for t in ev["tasks"] if t["status"] != "PASS"]
    assert skipped == [], f"casos não avaliados: {skipped}"
    out = agent_audit.ROOT / "progress/test-reports/agents_audit.json"  # relatório oficial (CI e local)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    assert report["summary"]["agents"] == 13 and report["eval_simulated"] is True
    for a in report["agents"]:
        failed = [k for k, v in a["checks"].items() if not v]
        assert not failed, f"{a['agent']}: {failed}"
        assert a["levels"]["ENGINEERING_READY"] and a["levels"]["MOCK_EVALUATED"]
        assert a["levels"]["REAL_MODEL_PENDING"] and not a["levels"]["PRODUCTION_READY"]
        assert all(a["red_team_cases"].values()), f"{a['agent']} sem caso de red-team"


def test_without_evaluation_nothing_counts_as_mock_evaluated():
    report = agent_audit.audit(None)
    assert report["summary"]["mock_evaluated"] == 0 and report["summary"]["production_ready"] == 0
