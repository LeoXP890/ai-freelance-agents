"""Busca semântica da base de conhecimento (auditoria A15; AI-008): embedder do Gemini desligado por padrão, vetores
com procedência (nunca compara espaços diferentes), custo no teto mensal de IA, queda para busca textual quando o
embedder não está disponível e reindexação ao trocar de embedder."""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy import select

from aicommerce.ai import knowledge, llm
from aicommerce.db import session_scope
from aicommerce.models import EMBEDDING_DIM, AIUsage, KBChunk, RetrievalAudit
from tests.test_ai import doc

CONCEPTS = {  # "significado" de brinquedo: sinônimos caem na mesma dimensão
    0: {"celular", "telefone", "smartphone", "aparelho"},
    1: {"garrafa", "termica", "squeeze"},
    2: {"capinha", "capa", "protecao", "case"},
}


class ConceptEmbedder:
    name = "fake-semantico-v1"
    min_similarity = 0.5

    def _vec(self, t: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIM
        for w in knowledge.tokens(t):
            for dim, group in CONCEPTS.items():
                if w in group:
                    v[dim] += 1.0
        v[EMBEDDING_DIM - 1] = 0.01  # nunca nulo
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]

    def embed_documents(self, db, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, db, t):
        return self._vec(t)


class Down(ConceptEmbedder):
    def embed_query(self, db, t):
        raise knowledge.EmbeddingUnavailable("teto mensal de IA atingido (embeddings)")


@pytest.fixture
def embedder():
    yield knowledge.set_embedder
    knowledge.set_embedder(None)
    with session_scope() as db:  # volta todos os vetores ao embedder padrão (o banco de teste é compartilhado)
        knowledge.reindex(db)


def search_ids(client, h, q):
    return [i["document_id"] for i in client.get("/v1/knowledge/search", params={"q": q}, headers=h).json()["items"]]


def test_semantic_embedder_finds_synonyms_that_text_search_misses(client, auth, embedder):
    h = auth("operator")
    tag = uuid.uuid4().hex[:6]
    content = f"Capinhas de silicone {tag} para celular com proteção nas bordas."
    hashing_doc = doc(client, h, title=f"Capinhas {tag}", content=content)
    assert hashing_doc["id"] not in search_ids(client, h, "case para smartphone")  # lexical: sem palavra em comum
    embedder(ConceptEmbedder())
    semantic_doc = doc(
        client,
        h,
        title=f"Capinhas v2 {tag}",
        content=content + " Modelo novo.",
        source_url=f"sim://kb/{uuid.uuid4().hex}",
    )
    ids = search_ids(client, h, "case para smartphone")
    assert semantic_doc["id"] in ids  # achado pelo significado
    assert hashing_doc["id"] not in ids  # vetor de outro embedder nunca entra na comparação


def test_search_falls_back_to_text_when_embedder_is_unavailable(client, auth, embedder):
    h = auth("operator")
    tag = uuid.uuid4().hex[:8]
    d = doc(client, h, title=f"Garantia {tag}", content=f"Garantia {tag} de noventa dias para defeitos de fabricação.")
    embedder(Down())
    r = client.get("/v1/knowledge/search", params={"q": f"garantia {tag}"}, headers=h)
    assert r.status_code == 200 and d["id"] in [i["document_id"] for i in r.json()["items"]]
    with session_scope() as db:
        last = db.execute(select(RetrievalAudit).order_by(RetrievalAudit.id.desc()).limit(1)).scalar_one()
        assert last.excluded["vector_unavailable"] == 1


def test_ingest_refuses_cleanly_when_embedder_cannot_index(client, auth, embedder):
    class NoIndex(ConceptEmbedder):
        def embed_documents(self, db, texts):
            raise knowledge.EmbeddingUnavailable("Gemini indisponível (TimeoutError)")

    embedder(NoIndex())
    body = {
        "collection": "atendimento",
        "title": "Doc",
        "source_url": f"sim://kb/{uuid.uuid4().hex}",
        "publisher": "AI Commerce",
        "document_type": "politica-interna",
        "access_policy": ["all", "agents"],
        "content": "O cliente pode solicitar troca em até 30 dias com a nota fiscal e a embalagem original.",
    }
    r = client.post("/v1/knowledge/documents", headers=auth("operator"), json=body)
    assert r.status_code == 503 and "indexar" in r.text  # indisponível agora ≠ entrada inválida


def test_reindex_moves_every_chunk_to_the_current_embedder(client, auth, embedder):
    doc(client, auth("operator"), content=f"Trecho {uuid.uuid4().hex} para reindexar com o embedder novo da base.")
    embedder(ConceptEmbedder())
    with session_scope() as db:
        r = knowledge.reindex(db, batch=7)
        assert r["remaining"] == 0 and r["embedder"] == "fake-semantico-v1" and r["reindexed"] >= 1
        assert set(db.execute(select(KBChunk.embedder).distinct()).scalars()) == {"fake-semantico-v1"}
        assert knowledge.reindex(db)["reindexed"] == 0  # idempotente


# ── cliente do Gemini (HTTP falso) ──
class FakeGemini:
    def __init__(self, status=200):
        self.calls: list[tuple[str, dict, dict]] = []
        self.status = status

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body))
        if self.status != 200:
            return self.status, {"error": {"message": "Resource has been exhausted"}}
        return 200, {"embeddings": [{"values": [3.0] + [0.0] * (EMBEDDING_DIM - 1)} for _ in body["requests"]]}


def test_gemini_embedder_request_shape_batches_normalizes_and_records_cost():
    api = FakeGemini()
    g = knowledge.GoogleEmbedder("chave-teste", "gemini-embedding-001", post=api)
    with session_scope() as db:
        before = db.execute(select(AIUsage).where(AIUsage.tier == "embedding")).scalars().all()
        vs = g.embed_documents(db, [f"trecho {i}" for i in range(250)])
        q = g.embed_query(db, "pergunta")
        after = db.execute(select(AIUsage).where(AIUsage.tier == "embedding")).scalars().all()
    assert len(vs) == 250 and len(api.calls) == 4  # 100 + 100 + 50 documentos + 1 consulta
    url, headers, body = api.calls[0]
    assert url.endswith("/models/gemini-embedding-001:batchEmbedContents") and "key=" not in url
    assert headers["x-goog-api-key"] == "chave-teste"
    first = body["requests"][0]
    assert first["taskType"] == "RETRIEVAL_DOCUMENT" and first["outputDimensionality"] == EMBEDDING_DIM
    assert api.calls[-1][2]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
    assert vs[0][0] == pytest.approx(1.0) and q[0] == pytest.approx(1.0)  # renormalizado após reduzir a dimensão
    new = after[len(before) :]
    assert len(new) == 4 and all(u.provider == "google" and not u.simulated for u in new)
    assert g.name == f"google:gemini-embedding-001:{EMBEDDING_DIM}"


def test_gemini_embedder_respects_monthly_budget_and_maps_errors(monkeypatch):
    api = FakeGemini()
    g = knowledge.GoogleEmbedder("k", "gemini-embedding-001", post=api)
    monkeypatch.setattr(llm, "budget_remaining", lambda db: 0)
    with session_scope() as db, pytest.raises(knowledge.EmbeddingUnavailable, match="teto"):
        g.embed_query(db, "pergunta")
    assert api.calls == []  # sem saldo: nem chama
    monkeypatch.undo()
    g429 = knowledge.GoogleEmbedder("k", "gemini-embedding-001", post=FakeGemini(status=429))
    with session_scope() as db, pytest.raises(knowledge.EmbeddingUnavailable, match="429"):
        g429.embed_query(db, "pergunta")
