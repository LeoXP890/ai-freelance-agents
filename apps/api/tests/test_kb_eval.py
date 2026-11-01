"""Avaliação de recuperação da base (A15): gabarito próprio, métricas por tipo de pergunta, nada fica na base real
e o custo de embeddings pagos sobrevive ao rollback da avaliação."""

from __future__ import annotations

from sqlalchemy import func, select

from aicommerce.ai import kb_eval, knowledge
from aicommerce.db import session_scope
from aicommerce.models import EMBEDDING_DIM, AIUsage, KBDocument


def eval_docs() -> int:
    with session_scope() as db:
        return db.execute(
            select(func.count()).select_from(KBDocument).where(KBDocument.source_url.like("sim://kb-eval/%"))
        ).scalar_one()


def test_lexical_engine_meets_acceptance_and_leaves_no_trace():
    r = kb_eval.run()
    assert r["embedder"] == "hashing-v1" and r["semantic"] is False
    assert r["lexical"]["recall_at_5"] >= kb_eval.LEXICAL_MIN_RECALL5 and r["acceptance"]["lexical_ok"]
    assert r["acceptance"]["semantic_ready_for_production"] is False  # paráfrase exige embedder semântico
    assert len(r["queries"]) == len(kb_eval.QUERIES) and eval_docs() == 0


def test_paid_embedding_cost_survives_the_evaluation_rollback():
    def fake_gemini(url, headers, body, timeout):  # devolve vetores do embedder léxico (determinístico)
        vecs = [knowledge.EMBEDDER.embed(req["content"]["parts"][0]["text"]) for req in body["requests"]]
        assert all(len(v) == EMBEDDING_DIM for v in vecs)
        return 200, {"embeddings": [{"values": v} for v in vecs]}

    knowledge.set_embedder(knowledge.GoogleEmbedder("k", "gemini-embedding-001", post=fake_gemini))
    try:
        with session_scope() as db:
            before = db.execute(
                select(func.count()).select_from(AIUsage).where(AIUsage.tier == "embedding")
            ).scalar_one()
        r = kb_eval.run()
        with session_scope() as db:
            after = db.execute(
                select(func.count()).select_from(AIUsage).where(AIUsage.tier == "embedding")
            ).scalar_one()
    finally:
        knowledge.set_embedder(None)
    assert r["semantic"] is True and r["embedding_cost_micro_usd"] > 0
    assert after - before == len(kb_eval.CORPUS) + len(kb_eval.QUERIES)  # 1 lote por documento + 1 por pergunta
    assert eval_docs() == 0
