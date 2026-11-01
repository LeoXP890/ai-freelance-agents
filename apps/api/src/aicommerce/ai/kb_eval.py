"""Avaliação da qualidade de recuperação da base de conhecimento (auditoria A15; AI-008).

Corpus próprio com gabarito: cada pergunta tem UM documento certo. Dois tipos de pergunta:
* `lexical`  — usa palavras do próprio documento (o que a busca textual deve acertar sempre);
* `paraphrase` — mesmo sentido, SEM palavras de conteúdo em comum (só um embedder semântico acerta).
Métricas: acerto no 1º lugar (recall@1), nos 5 primeiros (recall@5) e MRR, por tipo.

Roda numa transação desfeita no fim (a base real não é tocada); se o embedder for pago (Gemini), o custo das
chamadas é lançado de novo no ledger depois do rollback — o teto mensal nunca subconta.

Critérios de aceite (gravados no relatório):
* sempre: lexical recall@5 ≥ 0,90;
* para ligar AIC_KB_EMBEDDER=google em produção: paráfrase recall@5 ≥ 0,75 (o embedder léxico não precisa atingir).

Uso: python -m aicommerce.ai.kb_eval [--report caminho.json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
from dataclasses import dataclass

from sqlalchemy import func, select

from aicommerce.ai import knowledge
from aicommerce.db import session_scope
from aicommerce.models import AIUsage

LEXICAL_MIN_RECALL5 = 0.90
SEMANTIC_MIN_RECALL5 = 0.75


@dataclass(frozen=True)
class EvalDoc:
    key: str
    collection: str
    title: str
    content: str


CORPUS = [
    EvalDoc(
        "trocas",
        "atendimento",
        "Política de trocas",
        "O cliente pode pedir a troca do item em até 30 dias corridos após o recebimento, com a embalagem "
        "original e a nota fiscal. A segunda via da etiqueta de postagem é enviada por e-mail.",
    ),
    EvalDoc(
        "arrependimento",
        "atendimento",
        "Direito de arrependimento",
        "Nas compras online o consumidor pode desistir em até 7 dias do recebimento, sem justificar, e recebe "
        "o reembolso integral, inclusive do frete, conforme o artigo 49 do Código de Defesa do Consumidor.",
    ),
    EvalDoc(
        "postagem",
        "logistica",
        "Prazo de postagem do fornecedor",
        "O fornecedor parceiro despacha os pedidos em até dois dias úteis após a confirmação do pagamento e "
        "informa o código de rastreio, que é repassado automaticamente ao cliente.",
    ),
    EvalDoc(
        "garantia",
        "atendimento",
        "Garantia de produtos com defeito",
        "Itens com defeito de fabricação têm garantia legal de 90 dias para bens duráveis. O cliente envia "
        "fotos do defeito e o atendimento abre o reparo, a substituição ou a devolução do valor.",
    ),
    EvalDoc(
        "frete",
        "logistica",
        "Cálculo de frete",
        "O frete é calculado pelo peso cúbico do pacote e pelo CEP de destino. Acima de R$ 199 o envio é "
        "grátis para as regiões Sul e Sudeste; demais regiões pagam tarifa reduzida.",
    ),
    EvalDoc(
        "nfe",
        "legal-fiscal-br",
        "Emissão de nota fiscal eletrônica",
        "Toda venda gera NF-e emitida pelo provedor credenciado na SEFAZ, com o certificado digital A1 da "
        "empresa. A chave de acesso de 44 dígitos acompanha o pedido no painel.",
    ),
    EvalDoc(
        "lgpd",
        "global-policies",
        "Privacidade e dados pessoais",
        "Dados de clientes são cifrados e acessados só por quem tem permissão, com registro de cada consulta. "
        "O titular pode pedir acesso, correção ou exclusão das suas informações conforme a LGPD.",
    ),
    EvalDoc(
        "preco",
        "global-policies",
        "Política de preço mínimo",
        "Nenhum anúncio pode ter margem de contribuição negativa: o preço mínimo cobre custo do produto, "
        "tarifas do canal, frete subsidiado e impostos estimados pelo perfil tributário aprovado.",
    ),
    EvalDoc(
        "horario",
        "atendimento",
        "Horário do atendimento",
        "O suporte humano responde de segunda a sexta, das 9h às 18h. Mensagens fora desse horário recebem "
        "resposta automática e entram na fila do próximo dia útil.",
    ),
    EvalDoc(
        "tarifas_ml",
        "marketplaces",
        "Tarifas do Mercado Livre",
        "O anúncio clássico cobra comissão sobre o valor da venda e o premium cobra comissão maior com "
        "parcelamento sem juros; vendas abaixo de R$ 79 pagam também um custo fixo por unidade.",
    ),
    EvalDoc(
        "anvisa",
        "produto-compliance",
        "Produtos regulados pela Anvisa",
        "Cosméticos, saneantes e itens de saúde exigem notificação ou registro na Anvisa antes da venda. "
        "Sem o número do registro o anúncio fica bloqueado para revisão de conformidade.",
    ),
    EvalDoc(
        "sla_fornecedor",
        "fornecedores",
        "Acordo de nível de serviço do fornecedor",
        "O fornecedor se compromete com 98% dos envios no prazo e taxa de avaria abaixo de 1%. Descumprimento "
        "por dois meses seguidos suspende novas compras até um plano de ação.",
    ),
]

# (tipo, pergunta, documento certo)
QUERIES = [
    ("lexical", "troca em 30 dias com nota fiscal", "trocas"),
    ("lexical", "desistir da compra em 7 dias arrependimento", "arrependimento"),
    ("lexical", "prazo de postagem do fornecedor parceiro", "postagem"),
    ("lexical", "garantia para defeito de fabricação", "garantia"),
    ("lexical", "cálculo do frete pelo CEP e peso cúbico", "frete"),
    ("lexical", "emissão de NF-e com certificado A1", "nfe"),
    ("lexical", "LGPD exclusão de dados pessoais", "lgpd"),
    ("lexical", "preço mínimo e margem de contribuição", "preco"),
    ("lexical", "horário do suporte humano", "horario"),
    ("lexical", "comissão do anúncio clássico e premium no Mercado Livre", "tarifas_ml"),
    ("lexical", "registro na Anvisa de cosméticos", "anvisa"),
    ("lexical", "taxa de avaria e envios no prazo do fornecedor", "sla_fornecedor"),
    ("paraphrase", "posso mudar o tamanho da roupa que comprei mês passado?", "trocas"),
    ("paraphrase", "me arrependi, quero o dinheiro de volta sem explicar o motivo", "arrependimento"),
    ("paraphrase", "quando a mercadoria sai do depósito?", "postagem"),
    ("paraphrase", "o aparelho quebrou sozinho, o que acontece?", "garantia"),
    ("paraphrase", "quanto custa mandar para Manaus?", "frete"),
    ("paraphrase", "documento tributário que acompanha cada venda", "nfe"),
    ("paraphrase", "quero apagar meu cadastro da loja", "lgpd"),
    ("paraphrase", "não vender abaixo do custo", "preco"),
    ("paraphrase", "vocês respondem no sábado à noite?", "horario"),
    ("paraphrase", "quanto o marketplace amarelo fica de cada venda?", "tarifas_ml"),
    ("paraphrase", "posso vender shampoo e álcool em gel?", "anvisa"),
    ("paraphrase", "o que acontece se o parceiro atrasa entregas repetidamente?", "sla_fornecedor"),
]


def run() -> dict:
    emb = knowledge.get_embedder()
    principals = {"all", "agents", "owner"}
    with session_scope() as db:
        first_usage = db.execute(select(func.coalesce(func.max(AIUsage.id), 0))).scalar_one()
        ids: dict[str, str] = {}
        for d in CORPUS:
            meta = knowledge.DocMeta(
                collection=d.collection,
                title=f"[avaliação] {d.title}",
                source_url=f"sim://kb-eval/{d.key}",
                publisher="Avaliação de recuperação",
                document_type="politica-interna",
                owner="kb-eval",
                access_policy=["all"],
            )
            doc, _ = knowledge.ingest(db, meta, d.content, actor="kb-eval")
            ids[str(doc.id)] = d.key
        rows = []
        for kind, q, want in QUERIES:
            hits = knowledge.search(db, q, requester="kb-eval", principals=principals, k=5)
            got = [ids.get(h.document_id) for h in hits]
            rank = got.index(want) + 1 if want in got else None
            rows.append({"type": kind, "query": q, "expected": want, "rank": rank, "top": got[:3]})
        # custo de embeddings pagos sobrevive ao rollback (o teto mensal nunca subconta)
        paid = [
            {c.name: getattr(u, c.name) for c in AIUsage.__table__.columns if c.name not in ("id", "created_at")}
            for u in db.execute(select(AIUsage).where(AIUsage.id > first_usage, AIUsage.request_cost_micro_usd > 0))
            .scalars()
            .all()
        ]
        db.rollback()  # nada do corpus de avaliação fica na base real
    if paid:
        with session_scope() as db:
            db.add_all(AIUsage(**p) for p in paid)

    def metrics(kind: str) -> dict:
        rs = [r for r in rows if r["type"] == kind]
        return {
            "queries": len(rs),
            "recall_at_1": round(sum(1 for r in rs if r["rank"] == 1) / len(rs), 3),
            "recall_at_5": round(sum(1 for r in rs if r["rank"]) / len(rs), 3),
            "mrr": round(sum(1 / r["rank"] for r in rs if r["rank"]) / len(rs), 3),
        }

    lexical, paraphrase = metrics("lexical"), metrics("paraphrase")
    return {
        "embedder": emb.name,
        "semantic": not emb.name.startswith("hashing"),
        "lexical": lexical,
        "paraphrase": paraphrase,
        "acceptance": {
            "lexical_ok": lexical["recall_at_5"] >= LEXICAL_MIN_RECALL5,
            "semantic_ready_for_production": paraphrase["recall_at_5"] >= SEMANTIC_MIN_RECALL5,
            "criteria": f"lexical recall@5 ≥ {LEXICAL_MIN_RECALL5}; para ligar o embedder semântico: "
            f"paráfrase recall@5 ≥ {SEMANTIC_MIN_RECALL5}",
        },
        "embedding_cost_micro_usd": sum(int(p["request_cost_micro_usd"]) for p in paid),
        "queries": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=pathlib.Path)
    a = ap.parse_args()
    r = run()
    text = json.dumps(r, ensure_ascii=False, indent=2)
    if a.report:
        a.report.write_text(text + "\n")
    print(json.dumps({k: v for k, v in r.items() if k != "queries"}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if r["acceptance"]["lexical_ok"] else 1)


if __name__ == "__main__":
    main()
