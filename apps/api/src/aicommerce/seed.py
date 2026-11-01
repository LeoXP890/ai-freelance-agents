"""Seed idempotente de dados de referência. Usuários de demonstração somente em DRY_RUN.

Uso: python -m aicommerce.seed
"""

from __future__ import annotations

import datetime as dt
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.canonical import sha256_of
from aicommerce.config import get_settings
from aicommerce.db import session_scope
from aicommerce.finance import ledger
from aicommerce.governance import policy
from aicommerce.governance.golive import GATE_CATALOG
from aicommerce.identity import rbac, service
from aicommerce.models import (
    Agent,
    AgentConfig,
    AgentPresence,
    BudgetPolicy,
    CertificationGate,
    ChannelFeeSchedule,
    Permission,
    PolicyVersion,
    Product,
    Role,
    RolePermission,
    Supplier,
    SupplierDocument,
    SupplierOffer,
    TaxProfile,
    User,
    Variant,
)
from aicommerce.workforce.registry import AGENTS, DEFAULT_LIMITS, OUTPUT_SCHEMA

DEFAULT_ACCESSORY = {
    "atlas": "glasses",
    "eva": "headset",
    "pixel": "cap",
    "iris": "hat",
    "logan": "cap",
    "clara": "glasses",
    "orion": "glasses",
}
SIM_ACCOUNT = "sim-marketing-brl"


def seed_reference(db: Session) -> None:
    for code, desc in rbac.PERMISSIONS.items():
        db.merge(Permission(code=code, description=desc))
    for name, (desc, mfa, perms) in rbac.ROLES.items():
        db.merge(Role(name=name, description=desc, requires_mfa=mfa))
        db.flush()
        for p in perms:
            db.merge(RolePermission(role_name=name, permission_code=p))
    for a in AGENTS:
        agent = db.get(Agent, a["id"])
        if agent is None:
            agent = Agent(id=a["id"], name=a["name"], role=a["role"], room=a["room"], color=a["color"], appearance={})
            db.add(agent)
        else:  # nome/papel/sala vêm do código; cor e aparência são configuráveis pelo usuário e não são sobrescritas
            agent.name, agent.role, agent.room = a["name"], a["role"], a["room"]
        if not agent.appearance:
            agent.appearance = {"accessory": DEFAULT_ACCESSORY.get(a["id"], "none"), "skin": "#f1e3d3"}
        db.flush()
        if db.get(AgentPresence, a["id"]) is None:
            db.add(AgentPresence(agent_id=a["id"], state="IDLE", last_seq=0))
        # Configuração versionada (FR-002): se a definição no código mudou, grava uma NOVA versão (nunca edita).
        body = {
            "objectives": [a["role"]],
            "tools": a["tools"],
            "limits": DEFAULT_LIMITS,
            "output_schema": OUTPUT_SCHEMA,
        }
        latest = (
            db.execute(select(AgentConfig).where(AgentConfig.agent_id == a["id"]).order_by(AgentConfig.version.desc()))
            .scalars()
            .first()
        )
        if latest is None or latest.content_sha256 != sha256_of(body):
            db.add(
                AgentConfig(
                    agent_id=a["id"],
                    version=(latest.version + 1) if latest else 1,
                    content_sha256=sha256_of(body),
                    **body,
                )
            )
    for g in GATE_CATALOG:
        if db.get(CertificationGate, g["code"]) is None:
            db.add(CertificationGate(state="NOT_STARTED", scope={}, **g))
    seed_commerce_reference(db)
    from aicommerce.governance import seller

    seller.seed_eligibility(db)  # perfil PF (decisão do proprietário) + matriz por canal: nada comprovado
    for version, rules in policy.POLICY_HISTORY:
        if db.get(PolicyVersion, version) is None:
            db.add(PolicyVersion(version=version, rules=rules, content_sha256=sha256_of(rules), is_active=False))
    db.flush()
    for pv in db.execute(select(PolicyVersion)).scalars():  # exatamente uma versão ativa: a corrente
        pv.is_active = pv.version == policy.POLICY_CURRENT_VERSION
    db.flush()


FEE_NOTE = (
    "VALORES FICTÍCIOS para simulação — não são as tarifas reais do canal; substituir pela tabela oficial "
    "na homologação (verified=false até lá)."
)
FEE_SCHEDULES = {  # canal: (comissão bps, meio de pagamento bps, tarifa fixa em centavos)
    "WOOCOMMERCE": (0, 399, 0),
    "MERCADO_LIVRE": (1400, 0, 600),
    "SHOPEE": (1400, 0, 300),
    "TIKTOK_SHOP": (1200, 0, 400),
}


def seed_commerce_reference(db: Session) -> None:
    """Parâmetros de preço sempre presentes, explicitamente não verificados (nunca autorizam operação real)."""
    for channel, (comm, pay, fixed) in FEE_SCHEDULES.items():
        exists = db.execute(select(ChannelFeeSchedule).where(ChannelFeeSchedule.channel == channel)).first()
        if not exists:
            db.add(
                ChannelFeeSchedule(
                    channel=channel,
                    version=1,
                    commission_bps=comm,
                    payment_fee_bps=pay,
                    fixed_fee_minor=fixed,
                    verified=False,
                    source_note=FEE_NOTE,
                    is_active=True,
                )
            )
    if db.get(TaxProfile, "SIMULADO_PLACEHOLDER") is None:
        db.add(
            TaxProfile(
                code="SIMULADO_PLACEHOLDER",
                effective_rate_bps=600,
                approved_by_accountant=False,
                is_default=True,
                description="Alíquota efetiva FICTÍCIA de 6% para simulação. Não é parecer fiscal; "
                "definir com contador (gate TAX-01).",
            )
        )
    db.flush()


def seed_demo_catalog(db: Session) -> None:
    """Catálogo de demonstração (DRY_RUN). Fornecedor nacional pronto para aprovação humana pela UI."""
    if db.execute(select(Product).where(Product.sku_base == "SIM-GARRAFA-500")).first():
        return
    now = dt.datetime.now(dt.UTC)
    garrafa = Product(
        sku_base="SIM-GARRAFA-500",
        title="Garrafa térmica inox 500 ml (SIMULADO)",
        description="Parede dupla, mantém bebidas quentes ou frias. Produto de demonstração.",
        category="Casa e cozinha > Garrafas térmicas",
        brand="Exemplo",
        ncm="96170010",
        images=[],
        status="ACTIVE",
        is_simulated=True,
    )
    organizador = Product(
        sku_base="SIM-ORG-CABOS",
        title="Organizador de cabos de silicone (SIMULADO)",
        description="Kit com 6 presilhas. Produto de demonstração.",
        category="Eletrônicos > Acessórios",
        images=[],
        status="ACTIVE",
        is_simulated=True,
    )
    db.add_all([garrafa, organizador])
    db.flush()
    v_preta = Variant(
        product_id=garrafa.id,
        sku="SIM-GARRAFA-500-PRETA",
        attributes={"cor": "preta"},
        gtin="7890000000017",
        weight_g=380,
        length_mm=75,
        width_mm=75,
        height_mm=260,
    )
    v_branca = Variant(
        product_id=garrafa.id,
        sku="SIM-GARRAFA-500-BRANCA",
        attributes={"cor": "branca"},
        weight_g=380,
        length_mm=75,
        width_mm=75,
        height_mm=260,
    )
    v_org = Variant(
        product_id=organizador.id,
        sku="SIM-ORG-CABOS-6",
        attributes={"unidades": "6"},
        weight_g=60,
        length_mm=120,
        width_mm=80,
        height_mm=20,
    )
    db.add_all([v_preta, v_branca, v_org])
    nacional = Supplier(
        name="Distribuidora Exemplo Ltda (SIMULADO)",
        legal_id="00.000.000/0001-00 (fictício)",
        origin="NATIONAL",
        country="BR",
        status="EVALUATING",
        sla_dispatch_days=2,
        sample_status="APPROVED",
        notes="Fornecedor fictício para demonstração.",
        is_simulated=True,
    )
    exterior = Supplier(
        name="Global Sourcing Co. (SIMULADO)",
        origin="INTERNATIONAL",
        country="CN",
        status="PROSPECT",
        sla_dispatch_days=7,
        notes="Fornecedor fictício; exige gate TAX-02.",
        is_simulated=True,
    )
    db.add_all([nacional, exterior])
    db.flush()
    for doc_type in ("CNPJ_CARD", "CONTRACT", "INVOICE_SAMPLE"):
        ref = f"sim://documentos/distribuidora-exemplo/{doc_type.lower()}.pdf"
        db.add(
            SupplierDocument(
                supplier_id=nacional.id,
                doc_type=doc_type,
                reference=ref,
                sha256=sha256_of(ref),
                verified_by="seed (SIMULADO)",
                verified_at=now,
            )
        )
    for v, cost in ((v_preta, 3290), (v_branca, 3290), (v_org, 890)):
        db.add(
            SupplierOffer(
                supplier_id=nacional.id,
                variant_id=v.id,
                cost_minor=cost,
                lead_time_days=2,
                stock_qty=120,
                stock_observed_at=now,
                source_ref="sim://cotacao/distribuidora-exemplo",
            )
        )
    db.add(
        SupplierOffer(
            supplier_id=exterior.id,
            variant_id=v_preta.id,
            cost_minor=1850,
            moq=50,
            lead_time_days=25,
            stock_qty=5000,
            stock_observed_at=now,
            source_ref="sim://cotacao/global-sourcing",
        )
    )
    db.flush()


DEMO_KB = [
    (
        "atendimento",
        "Política de atendimento (SIMULADA)",
        "politica-interna",
        ["all", "agents"],
        "Atendimento de segunda a sexta, das 9h às 18h, pelo chat da loja. Resposta em até 1 dia útil. "
        "Pedidos de reembolso ou troca são analisados e precisam de aprovação humana antes de qualquer promessa.",
    ),
    (
        "logistica",
        "Prazos de expedição dos fornecedores (SIMULADO)",
        "politica-interna",
        ["all", "agents"],
        "O fornecedor nacional parceiro posta em até 2 dias úteis após a confirmação da compra. "
        "O rastreio é informado ao cliente assim que o fornecedor envia o código.",
    ),
    (
        "marketplaces",
        "Boas práticas de anúncio (SIMULADO)",
        "guia",
        ["all", "agents"],
        "Anúncios com fotos reais, título objetivo de até 60 caracteres e GTIN preenchido tendem a converter melhor. "
        "Evite promessas absolutas como 'garantido' ou '100%'.",
    ),
    (
        "legal-fiscal-br",
        "CDC art. 49 — direito de arrependimento (resumo interno)",
        "norma",
        ["all", "agents"],
        "Resumo interno, pendente de revisão: nas compras fora do estabelecimento comercial o consumidor pode desistir "
        "em 7 dias a contar do recebimento, com devolução dos valores pagos. Fonte oficial: Planalto.",
    ),
]


def seed_demo_knowledge(db: Session) -> None:
    from aicommerce.ai import knowledge

    for collection, title, doc_type, acl, content in DEMO_KB:
        url = (
            "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm"
            if doc_type == "norma"
            else f"sim://kb/{collection}/{sha256_of(title)[:8]}"
        )
        meta = knowledge.DocMeta(
            collection=collection,
            title=title,
            source_url=url,
            publisher="Planalto (resumo interno)" if doc_type == "norma" else "AI Commerce (SIMULADO)",
            document_type=doc_type,
            owner="seed",
            access_policy=acl,
        )
        knowledge.ingest(db, meta, content, actor="seed")


DEMO_CATEGORY_MAP = {  # categorias FICTÍCIAS (verified=false) só para a demonstração
    "MERCADO_LIVRE": {"Casa e cozinha > Garrafas térmicas": "SIM-MLB1001", "Eletrônicos > Acessórios": "SIM-MLB1002"},
    "SHOPEE": {"Casa e cozinha > Garrafas térmicas": "900001", "Eletrônicos > Acessórios": "900002"},
    "TIKTOK_SHOP": {"Casa e cozinha > Garrafas térmicas": "SIM-600001", "Eletrônicos > Acessórios": "SIM-600002"},
}


def seed_demo_channels(db: Session) -> None:
    from aicommerce.commerce import channels
    from aicommerce.models import ChannelAccount, ChannelCategoryMap

    for ch, cats in DEMO_CATEGORY_MAP.items():
        if not db.execute(select(ChannelAccount).where(ChannelAccount.channel == ch)).first():
            channels.connect_simulated(db, ch, actor="seed")
        for internal, external in cats.items():
            if not db.execute(
                select(ChannelCategoryMap).where(
                    ChannelCategoryMap.channel == ch, ChannelCategoryMap.internal_category == internal
                )
            ).first():
                db.add(
                    ChannelCategoryMap(
                        channel=ch,
                        internal_category=internal,
                        external_category_id=external,
                        verified=False,
                        created_by="seed",
                    )
                )
    db.flush()


def seed_simulation(db: Session) -> dict:
    """Conta de orçamento simulada e usuários de demonstração (DRY_RUN apenas)."""
    s = get_settings()
    if not s.is_dry_run:
        raise RuntimeError("seed de simulação proibido fora de DRY_RUN")
    acc = ledger.ensure_account(db, SIM_ACCOUNT, "Mídia (SIMULADO)")
    if db.get(BudgetPolicy, acc.id) is None:
        ledger.set_budget(db, acc.id, approved_cap_minor=500_000, safety_hold_minor=50_000, approved_by="seed")
    compras = ledger.ensure_account(db, "sim-compras-brl", "Compras de fornecedor (SIMULADO)")
    if db.get(BudgetPolicy, compras.id) is None:
        ledger.set_budget(db, compras.id, approved_cap_minor=2_000_000, safety_hold_minor=0, approved_by="seed")
    out = {"account_id": str(acc.id), "users": []}
    if not s.seed_demo_users:
        return out
    seed_demo_catalog(db)
    seed_demo_knowledge(db)
    seed_demo_channels(db)
    password = os.environ.get("AIC_DEMO_PASSWORD", "simulado-123")
    for email, name, roles in (
        ("owner@demo.local", "Proprietário (demo)", ["owner"]),
        ("operator@demo.local", "Operador (demo)", ["operator"]),
        ("viewer@demo.local", "Leitor (demo)", ["viewer"]),
    ):
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            user = service.create_user(
                db,
                email=email,
                display_name=name,
                password=password,
                roles=roles,
                simulated=True,
            )
        # Papéis com MFA cadastram o fator com QR code no primeiro login (nenhum segredo em log).
        out["users"].append({"email": email, "roles": roles, "mfa_pending": user.totp_secret is None})
    return out


def main() -> None:
    with session_scope() as db:
        seed_reference(db)
        info = seed_simulation(db) if get_settings().is_dry_run else {}
    print("Seed concluído.", "Modo:", get_settings().mode.value)
    for u in info.get("users", []):
        print(f"  {u['email']} {u['roles']}")


if __name__ == "__main__":
    main()
