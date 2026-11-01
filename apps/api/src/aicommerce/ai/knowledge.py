"""Knowledge Hub (AI-008; SDD §11): ingestão versionada, chunking, embeddings, busca híbrida FTS + pgvector,
ACL, validade, revisão humana para normas, auditoria de recuperação e roteador de consultas.

Embeddings: `HashingEmbedder` (feature hashing determinístico, 256 dims) — similaridade LÉXICA, sem rede nem custo;
é o padrão até a IA real ser ligada. `GoogleEmbedder` (AIC_KB_EMBEDDER=google): embeddings SEMÂNTICOS do Gemini
(256 dims, tarefa de documento × consulta), custo no mesmo teto mensal de IA. Cada vetor guarda o nome do embedder:
a busca vetorial só compara vetores do embedder atual (espaços diferentes não se misturam) e o FTS continua valendo
para todos; `python -m aicommerce.manage reindex-kb` regrava os vetores antigos.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import math
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, func, select, text
from sqlalchemy.orm import Session

from aicommerce.ai.llm import looks_like_injection
from aicommerce.canonical import sha256_of
from aicommerce.config import get_settings
from aicommerce.governance import audit
from aicommerce.models import EMBEDDING_DIM, KBChunk, KBDocument, RetrievalAudit

COLLECTIONS = {
    "global-policies",
    "dropshipping",
    "fornecedores",
    "marketing",
    "plataformas-apis",
    "legal-fiscal-br",
    "produto-compliance",
    "atendimento",
    "logistica",
    "analytics",
    "experimentos",
    "marketplaces",
}
NORM_TYPES = {"norma", "lei", "decreto", "solucao-consulta", "parecer"}  # exigem revisão humana
OFFICIAL_PUBLISHERS = ("planalto.gov.br", "receita.fazenda.gov.br", "nfe.fazenda.gov.br", "gov.br")
STOPWORDS = set(
    "a o os as de da do das dos e em no na nos nas um uma uns umas para por com sem que se ao à às "
    "é ser são foi como mais menos não sim ou the and of to in".split()
)


class KnowledgeError(Exception):
    code = "KNOWLEDGE_ERROR"
    http = 409


class KnowledgeUnavailable(KnowledgeError):
    """Indexação impossível agora (embedder fora do ar ou teto de IA): tentar depois, não é erro de entrada."""

    code = "KNOWLEDGE_UNAVAILABLE"
    http = 503


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def tokens(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]{3,}", _norm(t)) if w not in STOPWORDS]


class HashingEmbedder:
    dim = EMBEDDING_DIM
    name = "hashing-v1"
    min_similarity = 0.15  # abaixo disso não há termos em comum: não é resultado

    def embed_documents(self, db: Session | None, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def embed_query(self, db: Session | None, t: str) -> list[float]:
        return self.embed(t)

    def embed(self, t: str) -> list[float]:
        ws = tokens(t)
        feats = ws + [f"{a}_{b}" for a, b in itertools.pairwise(ws)]
        v = [0.0] * self.dim
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


EMBEDDER = HashingEmbedder()


class EmbeddingUnavailable(Exception):
    """Embedder semântico indisponível agora (teto de IA, rede, cota). Consulta cai para busca textual."""


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class GoogleEmbedder:
    """Embeddings do Gemini (batchEmbedContents). Chave só no cabeçalho; dimensão reduzida a 256 (MRL) e
    renormalizada. Antes de cada lote confere o saldo do teto mensal de IA; o custo vai para o ledger ai_usage."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
    BATCH = 100

    def __init__(self, api_key: str, model: str, post=None, timeout: float = 60.0) -> None:
        from aicommerce.ai.llm import _http_post

        self._key, self.model, self._post, self._timeout = api_key, model, post or _http_post, timeout
        self.name = f"google:{model}:{EMBEDDING_DIM}"
        # similaridade de cosseno mínima para contar como resultado (CALIBRAR com a base real ao ativar)
        self.min_similarity = 0.5

    def _batch(self, db: Session | None, texts: list[str], task: str) -> list[list[float]]:
        from aicommerce.ai import llm

        est_tokens = sum(max(1, len(t) // 4) for t in texts)
        cost = llm.cost_micro_usd(self.model, est_tokens, 0, 0)
        if db is not None and llm.budget_remaining(db) < cost:
            raise EmbeddingUnavailable("teto mensal de IA atingido (embeddings)")
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t[:8000]}]},
                    "taskType": task,
                    "outputDimensionality": EMBEDDING_DIM,
                }
                for t in texts
            ]
        }
        try:
            status, data = self._post(
                self.URL.format(model=self.model),
                {"Content-Type": "application/json", "x-goog-api-key": self._key},
                body,
                self._timeout,
            )
        except (TimeoutError, OSError) as e:
            raise EmbeddingUnavailable(f"Gemini indisponível ({type(e).__name__})") from e
        if status != 200:
            msg = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
            raise EmbeddingUnavailable(f"Gemini recusou os embeddings (HTTP {status}: {msg[:200]})")
        vectors = [e.get("values") or [] for e in (data.get("embeddings") or [])]
        if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIM for v in vectors):
            raise EmbeddingUnavailable("resposta de embeddings fora do formato esperado")
        if db is not None:
            from aicommerce.models import AIUsage

            db.add(
                AIUsage(
                    agent_id="atlas",
                    purpose=f"kb.embed.{task.lower()}",
                    provider="google",
                    model=self.model,
                    tier="embedding",
                    route_reason="busca semântica da base de conhecimento",
                    input_tokens=est_tokens,
                    cached_tokens=0,
                    output_tokens=0,
                    request_cost_micro_usd=cost,
                    budget_remaining_micro_usd=llm.budget_remaining(db) - cost,
                    stop_reason="OK",
                    simulated=False,
                )
            )
        return [_normalize([float(x) for x in v]) for v in vectors]

    def embed_documents(self, db: Session | None, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            out += self._batch(db, texts[i : i + self.BATCH], "RETRIEVAL_DOCUMENT")
        return out

    def embed_query(self, db: Session | None, t: str) -> list[float]:
        return self._batch(db, [t], "RETRIEVAL_QUERY")[0]


_custom_embedder = None


def get_embedder():
    if _custom_embedder is not None:
        return _custom_embedder
    s = get_settings()
    if s.kb_embedder == "google":
        return GoogleEmbedder(s.google_api_key.get_secret_value(), s.google_embedding_model)
    return EMBEDDER


def set_embedder(e) -> None:
    """Testes: injeta um embedder (None volta ao configurado)."""
    global _custom_embedder
    _custom_embedder = e


def reindex(db: Session, *, batch: int = 100) -> dict[str, int]:
    """Regrava com o embedder atual os vetores gerados por outro. Lote por lote (pode ser retomado)."""
    emb = get_embedder()
    done = 0
    while True:
        rows = (
            db.execute(select(KBChunk).where(KBChunk.embedder != emb.name).order_by(KBChunk.id).limit(batch))
            .scalars()
            .all()
        )
        if not rows:
            break
        try:
            vectors = emb.embed_documents(db, [c.text for c in rows])
        except EmbeddingUnavailable as e:
            raise KnowledgeUnavailable(f"reindexação interrompida após {done} trechos: {e}") from e
        for c, v in zip(rows, vectors, strict=True):
            c.embedding, c.embedder = v, emb.name
        done += len(rows)
        db.flush()
    left = db.execute(select(func.count()).select_from(KBChunk).where(KBChunk.embedder != emb.name)).scalar_one()
    return {"reindexed": done, "remaining": int(left), "embedder": emb.name}


def chunk(t: str, size: int = 700, overlap: int = 120) -> list[str]:
    """Chunk 'semântico' simples: respeita parágrafos e frases; janelas com sobreposição."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paras:
        for sent in re.split(r"(?<=[.!?;])\s+", p):
            if len(buf) + len(sent) + 1 > size and buf:
                out.append(buf.strip())
                buf = buf[-overlap:] + " " + sent
            else:
                buf = (buf + " " + sent).strip()
        buf += "\n"
    if buf.strip():
        out.append(buf.strip())
    return out


@dataclass(frozen=True)
class DocMeta:
    collection: str
    title: str
    source_url: str
    publisher: str
    document_type: str
    owner: str
    access_policy: list[str]
    jurisdiction: str | None = "BR"
    channel: str | None = None
    language: str = "pt-BR"
    published_at: dt.datetime | None = None
    valid_from: dt.datetime | None = None
    valid_until: dt.datetime | None = None


def ingest(db: Session, meta: DocMeta, content: str, *, actor: str) -> tuple[KBDocument, bool]:
    """captura autorizada → validação da origem → limpeza → chunk → embedding → índice (FTS gerado pelo banco)."""
    if meta.collection not in COLLECTIONS:
        raise KnowledgeError(f"coleção inválida; use {sorted(COLLECTIONS)}")
    if not meta.access_policy:
        raise KnowledgeError("access_policy obrigatória (papéis/agentes com acesso)")
    cleaned = re.sub(r"[ \t]+", " ", "".join(ch for ch in content if ch in "\n\t" or ch.isprintable())).strip()
    if len(cleaned) < 40:
        raise KnowledgeError("conteúdo curto demais para indexar")
    content_hash = sha256_of(cleaned)
    dup = db.execute(
        select(KBDocument).where(KBDocument.collection == meta.collection, KBDocument.content_hash == content_hash)
    ).scalar_one_or_none()
    if dup:
        return dup, False
    is_norm = meta.document_type in NORM_TYPES
    suspicious = looks_like_injection(cleaned)
    official = (
        any(meta.source_url.split("/")[2].endswith(p) for p in OFFICIAL_PUBLISHERS)
        if meta.source_url.startswith("http")
        else False
    )
    now = dt.datetime.now(dt.UTC)
    prev = (
        db.execute(
            select(KBDocument)
            .where(KBDocument.source_url == meta.source_url, KBDocument.collection == meta.collection)
            .order_by(KBDocument.version.desc())
        )
        .scalars()
        .first()
    )
    doc = KBDocument(
        collection=meta.collection,
        title=meta.title,
        source_url=meta.source_url,
        publisher=meta.publisher,
        document_type=meta.document_type,
        jurisdiction=meta.jurisdiction,
        channel=meta.channel,
        language=meta.language,
        published_at=meta.published_at,
        verified_at=None,
        valid_from=meta.valid_from or now,
        valid_until=meta.valid_until,
        owner=meta.owner,
        access_policy=meta.access_policy,
        # Normas e conteúdo com indício de injeção passam por revisão humana antes de serem recuperáveis.
        approval_status="PENDING_REVIEW" if (is_norm or suspicious) else "APPROVED",
        content_hash=content_hash,
        version=(prev.version + 1) if prev else 1,
        suspicious=suspicious,
        is_simulated=get_settings().is_dry_run,
    )
    db.add(doc)
    db.flush()
    if prev and (prev.valid_until is None or prev.valid_until > now):
        prev.valid_until = now  # versão anterior expira
    emb = get_embedder()
    parts = chunk(cleaned)
    try:
        vectors = emb.embed_documents(db, parts)
    except EmbeddingUnavailable as e:
        raise KnowledgeUnavailable(f"não foi possível indexar o documento agora: {e}") from e
    for i, (c, v) in enumerate(zip(parts, vectors, strict=True)):
        db.add(KBChunk(document_id=doc.id, ordinal=i, text=c, embedding=v, embedder=emb.name))
    db.flush()
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="kb.document.ingested",
        subject_type="kb_document",
        subject_id=str(doc.id),
        payload={
            "collection": doc.collection,
            "version": doc.version,
            "approval_status": doc.approval_status,
            "suspicious": doc.suspicious,
            "official_source": official,
            "content_hash": content_hash,
        },
    )
    return doc, True


def approve(db: Session, doc_id: uuid.UUID, *, actor: str, approved: bool, note: str) -> KBDocument:
    doc = db.get(KBDocument, doc_id, with_for_update=True)
    if doc is None:
        raise KnowledgeError("documento inexistente")
    doc.approval_status = "APPROVED" if approved else "REJECTED"
    doc.verified_at = dt.datetime.now(dt.UTC)
    if approved:
        doc.suspicious = False  # revisor humano leu e liberou
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="kb.document.reviewed",
        subject_type="kb_document",
        subject_id=str(doc.id),
        payload={"approved": approved, "note": note},
    )
    return doc


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    document_id: str
    title: str
    collection: str
    source_url: str
    publisher: str
    published_at: str | None
    approval_status: str
    text: str
    score: float


def _allowed(doc: KBDocument, principals: set[str]) -> bool:
    return "all" in doc.access_policy or bool(principals & set(doc.access_policy))


def search(
    db: Session,
    query: str,
    *,
    requester: str,
    principals: set[str],
    collections: set[str] | None = None,
    k: int = 5,
    require_approved: bool = False,
    now: dt.datetime | None = None,
) -> list[Hit]:
    """Busca híbrida: FTS (português) + vetor (cosseno), fundidas por RRF; filtros de ACL/validade/revisão."""
    now = now or dt.datetime.now(dt.UTC)
    base = select(KBChunk.id).join(KBDocument, KBDocument.id == KBChunk.document_id)
    if collections:
        base = base.where(KBDocument.collection.in_(collections))
    # OU entre os termos (não E): pergunta natural tem palavras que não estão no trecho; a ordenação (ts_rank_cd)
    # põe no topo quem casa mais termos e mais próximos — medido em kb_eval (A15)
    tsq = func.to_tsquery(
        "portuguese", func.replace(func.cast(func.plainto_tsquery("portuguese", query), String), "&", "|")
    )
    fts = db.execute(
        base.add_columns(func.ts_rank_cd(KBChunk.tsv, tsq).label("r"))
        .where(KBChunk.tsv.op("@@")(tsq))
        .order_by(text("r DESC"))
        .limit(30)
    ).all()
    emb = get_embedder()
    try:  # vetor só contra vetores do MESMO embedder; indisponível = só texto (nunca falha a busca)
        qv = emb.embed_query(db, query)
        dist = KBChunk.embedding.cosine_distance(qv)
        max_dist = 1 - getattr(emb, "min_similarity", 0.0)  # vizinho distante não é resultado (antes entrava sempre)
        vec = db.execute(base.where(KBChunk.embedder == emb.name, dist <= max_dist).order_by(dist).limit(30)).all()
    except EmbeddingUnavailable:
        vec, degraded = [], True
    else:
        degraded = False
    rrf: dict[int, float] = {}
    for rank, row in enumerate(fts):
        rrf[row[0]] = rrf.get(row[0], 0) + 1 / (60 + rank)
    for rank, row in enumerate(vec):
        rrf[row[0]] = rrf.get(row[0], 0) + 1 / (60 + rank)
    excluded = {
        "acl": 0,
        "expired": 0,
        "unapproved": 0,
        "suspicious": 0,
        "rejected": 0,
        "vector_unavailable": int(degraded),
    }
    hits: list[Hit] = []
    for cid, score in sorted(rrf.items(), key=lambda kv: -kv[1]):
        c = db.get(KBChunk, cid)
        d = db.get(KBDocument, c.document_id)
        if not _allowed(d, principals):
            excluded["acl"] += 1
            continue
        if (d.valid_until and d.valid_until <= now) or (d.valid_from and d.valid_from > now):
            excluded["expired"] += 1
            continue
        if d.approval_status == "REJECTED":
            excluded["rejected"] += 1
            continue
        if d.suspicious and d.approval_status != "APPROVED":
            excluded["suspicious"] += 1
            continue
        if d.approval_status != "APPROVED" and (require_approved or d.document_type in NORM_TYPES):
            excluded["unapproved"] += 1
            continue
        hits.append(
            Hit(
                c.id,
                str(d.id),
                d.title,
                d.collection,
                d.source_url,
                d.publisher,
                d.published_at.isoformat() if d.published_at else None,
                d.approval_status,
                c.text,
                round(score, 5),
            )
        )
        if len(hits) >= k:
            break
    db.add(
        RetrievalAudit(
            requester=requester,
            query=query[:1000],
            route="retrieval",
            returned_chunk_ids=[h.chunk_id for h in hits],
            excluded=excluded,
        )
    )
    db.flush()
    return hits


# ───────────── Roteador de consultas (SQL × regra formal × documentos) ─────────────
SQL_HINTS = (
    "quanto",
    "quantos",
    "vendas",
    "faturamento",
    "margem",
    "estoque",
    "pedido",
    "pedidos",
    "receita",
    "custo",
    "gasto",
    "orçamento",
    "orcamento",
    "saldo",
    "repasse",
    "total",
    "média",
    "media",
)
RULE_HINTS = (
    "aprovação",
    "aprovacao",
    "aprovar",
    "política",
    "politica",
    "permitido",
    "pode publicar",
    "kill switch",
    "gate",
    "go-live",
    "golive",
    "bloqueado",
)


def route_query(q: str) -> str:
    n = _norm(q)
    if any(_norm(h) in n for h in RULE_HINTS):
        return "rule"
    if any(_norm(h) in n for h in SQL_HINTS):
        return "sql"
    return "retrieval"


def hits_as_sources(hits: list[Hit], prefix: str = "K") -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}{i + 1}",
            "title": h.title,
            "url": h.source_url,
            "publisher": h.publisher,
            "date": h.published_at,
            "text": h.text,
            "approval_status": h.approval_status,
            "chunk_id": h.chunk_id,
        }
        for i, h in enumerate(hits)
    ]
