"""Perfil do vendedor (Pessoa Física/Jurídica) e elegibilidade por canal — ADR 0006, adendo PF ao SDD v2.

Regras (nenhuma é permissiva por padrão):
* o perfil NÃO autoriza nada: CPF (ou CNPJ) não é autorização fiscal nem comercial;
* para operação real, cada canal precisa de elegibilidade comprovada (SUPPORTED/CONDITIONAL) EM CADA aspecto exigido
  pelo modelo de venda — cadastro, venda habitual, API, venda à ordem, envio e fiscal são verificados separadamente;
* elegibilidade comprovada também não autoriza: o go-live continua exigindo os gates (LEG-01, TAX-*, canal…), agora
  aprovados PARA o perfil em vigor e, em PF, com parecer profissional anexado;
* mudar o perfil ou a elegibilidade de um canal derruba as aprovações afetadas (precisam de nova revisão);
* DRY_RUN não depende de nada disto: a simulação segue funcionando com qualquer perfil.

O identificador fiscal (CPF/CNPJ) não é armazenado — não é necessário para o funcionamento atual.
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicommerce.governance import audit

ELIGIBILITY_POLICY_VERSION = "2026.10.pf2"  # pf2: evidência oficial TikTok Shop (CNPJ obrigatório)
PF_NOT_HOMOLOGATED = "Operação com Pessoa Física ainda não homologada para este modelo de venda."


class SellerType(enum.StrEnum):
    PERSON = "PERSON"  # Pessoa Física (CPF)
    COMPANY = "COMPANY"  # Pessoa Jurídica (CNPJ) — mantida para compatibilidade


class Eligibility(enum.StrEnum):
    SUPPORTED = "SUPPORTED"  # comprovada para o escopo analisado
    CONDITIONAL = "CONDITIONAL"  # permitida só sob condições verificadas
    UNVERIFIED = "UNVERIFIED"  # não confirmada
    UNSUPPORTED = "UNSUPPORTED"  # incompatibilidade comprovada


OK = (Eligibility.SUPPORTED.value, Eligibility.CONDITIONAL.value)
_RANK = {"UNSUPPORTED": 0, "UNVERIFIED": 1, "CONDITIONAL": 2, "SUPPORTED": 3}

ASPECTS = {
    "registration": "Cadastro da conta de vendedor no perfil",
    "habitual_sales": "Venda habitual de mercadorias no perfil",
    "api_access": "Acesso à API/integração para o perfil",
    "dropshipping": "Venda à ordem (fornecedor envia)",
    "shipping": "Modalidades de envio disponíveis ao perfil",
    "fiscal": "Obrigações fiscais e documento da venda no perfil",
}
FISCAL_MODELS = {
    "UNDEFINED": "A definir com profissional habilitado",
    "COMPANY_NFE": "Pessoa Jurídica com emissão de NF-e",
    "PERSON_PENDING_OPINION": "Pessoa Física — modelo fiscal aguardando parecer profissional",
}
# Gates cuja aprovação depende do perfil: valem só para o tipo de vendedor em que foram aprovados.
PROFILE_SENSITIVE_GATES = ("LEG-01", "TAX-01", "TAX-02", "TAX-03", "CH-WOO", "CH-ML", "CH-SHO", "CH-TTS")
# Em PF, estes gates exigem parecer profissional (PARECER) ou aceite de risco do proprietário (ACEITE_RISCO, só com
# aprovação com restrições — gates.decide).
PERSON_OPINION_GATES = ("LEG-01", "TAX-01", "TAX-02", "TAX-03")

# Textos dos gates por perfil (os registros históricos do banco não mudam; a API apresenta a versão do perfil).
GATE_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "PERSON": {
        "LEG-01": {
            "title": "Elegibilidade da Pessoa Física para o modelo de venda",
            "expected_evidence": "Parecer de profissional habilitado (contador/advogado) sobre a venda pretendida por "
            "Pessoa Física — habitualidade, canais, venda à ordem, UF — e as obrigações que se aplicam; "
            "confirmação da elegibilidade da conta PF em cada canal",
            "responsible": "Proprietário + contador/advogado",
            "blocks": "Toda venda real (o CPF sozinho não autoriza)",
        },
        "TAX-01": {
            "title": "Venda à ordem por Pessoa Física",
            "expected_evidence": "Parecer escrito sobre se e como a venda à ordem pode ser feita por Pessoa Física "
            "neste fluxo concreto (documentos do fornecedor e da venda)",
            "responsible": "Contador",
            "blocks": "Venda nacional com envio pelo fornecedor",
        },
        "TAX-02": {
            "title": "Importação por Pessoa Física",
            "expected_evidence": "Parecer sobre a modalidade de importação aplicável a Pessoa Física e seus limites",
            "responsible": "Contador/despachante",
            "blocks": "Cross-border real",
        },
        "TAX-03": {
            "title": "Documento fiscal da venda (Pessoa Física)",
            "expected_evidence": "Parecer sobre o documento fiscal exigido para a venda por Pessoa Física neste "
            "modelo e quem o emite (você, o canal ou um serviço), mais teste de homologação do emissor indicado",
            "responsible": "Proprietário + contador",
            "blocks": "Emissão/obrigação fiscal em produção",
        },
    }
}


@dataclass(frozen=True)
class EligibilitySnapshot:
    channel: str
    seller_type: str
    status: str
    aspects: dict[str, dict[str, str]]
    restrictions: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    conditions: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1


@dataclass(frozen=True)
class SellerContext:
    """O que o GoLivePolicy precisa saber do vendedor. Sem valor padrão para o perfil: quem avalia tem de dizê-lo.
    `fingerprints`: impressão, por gate, das propriedades do perfil que aquela homologação considerou."""

    seller_type: str
    eligibility: dict[str, EligibilitySnapshot]
    fingerprints: dict[str, str] = field(default_factory=dict)


# Condições verificáveis da elegibilidade: campo da condição → atributo da operação avaliada (GoLiveRequest).
CONDITION_FIELDS = {
    "ufs": "uf",
    "fulfillment_modes": "fulfillment_mode",
    "supplier_origins": "supplier_origin",
    "product_ids": "product_id",
    "supplier_ids": "supplier_id",
}
# Propriedades do perfil que cada homologação considera (mudança nelas = nova revisão; as demais não invalidam).
MATERIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "LEG-01": ("person_type", "uf", "fiscal_model", "restrictions"),
    "TAX-01": ("person_type", "uf", "fiscal_model"),
    "TAX-02": ("person_type", "fiscal_model"),
    "TAX-03": ("person_type", "uf", "fiscal_model"),
    "CH-WOO": ("person_type",),
    "CH-ML": ("person_type",),
    "CH-SHO": ("person_type",),
    "CH-TTS": ("person_type",),
}
_CHANNEL_OF_GATE = {"CH-WOO": "WOOCOMMERCE", "CH-ML": "MERCADO_LIVRE", "CH-SHO": "SHOPEE", "CH-TTS": "TIKTOK_SHOP"}


def fingerprints(profile: dict[str, Any], eligibility: dict[str, EligibilitySnapshot]) -> dict[str, str]:
    """Impressão por gate: hash das propriedades materiais do perfil (+ versão/estado/condições da elegibilidade do
    canal, nos gates de canal). Gravada na aprovação e conferida em toda avaliação: permissão obsoleta não vale."""
    from aicommerce.canonical import sha256_of

    out = {}
    for code, keys in MATERIAL_FIELDS.items():
        material: dict[str, Any] = {k: profile.get(k) for k in keys}
        ch = _CHANNEL_OF_GATE.get(code)
        if ch:
            e = eligibility.get(ch)
            material["eligibility"] = (
                {"version": e.version, "status": e.status, "conditions": e.conditions} if e else None
            )
        out[code] = sha256_of(material)
    return out


def check_conditions(conditions: list[dict[str, Any]], req: Any) -> list[str]:
    """Violações das condições para a operação concreta. Condição desconhecida, malformada ou que não pode ser
    comprovada (a operação não informa o campo) também nega — nunca é ignorada."""
    errs = []
    for c in conditions:
        f, allowed = c.get("field"), c.get("allowed")
        attr = CONDITION_FIELDS.get(str(f))
        if attr is None or not isinstance(allowed, list) or not allowed:
            errs.append(f"condição inválida ou não verificável: {f!r}")
            continue
        value = getattr(req, attr, None)
        value = getattr(value, "value", value)
        if value is None:
            errs.append(f"condição {f} não pode ser comprovada: a operação não informa {attr}")
        elif str(value).upper() not in {str(a).upper() for a in allowed}:
            errs.append(f"condição {f} violada: {value} fora de {allowed}")
    return errs


def overall(aspects: dict[str, dict[str, str]]) -> str:
    """Pior estado entre os aspectos (um aspecto não verificado deixa o canal não verificado)."""
    if set(aspects) != set(ASPECTS):
        return Eligibility.UNVERIFIED.value
    return min((a.get("status", "UNVERIFIED") for a in aspects.values()), key=lambda s: _RANK.get(s, 1))


def required_aspects(fulfillment_mode: str) -> list[str]:
    need = ["registration", "habitual_sales", "api_access", "shipping", "fiscal"]
    if fulfillment_mode == "DROPSHIP_SUPPLIER":
        need.append("dropshipping")
    return need


def eligibility_check(ctx: SellerContext, req: Any) -> tuple[str | None, list[str]]:
    """(motivo de bloqueio ou None, restrições). PF exige a matriz; PJ sem linha segue o desenho original (o gate
    do canal comprova a conta apta); PJ com linha cadastrada é avaliada igual. As condições estruturadas valem
    sempre (inclusive em SUPPORTED); aspecto CONDITIONAL sem condição verificável é negado."""
    channel = getattr(req.channel, "value", req.channel)
    fulfillment_mode = getattr(req.fulfillment_mode, "value", req.fulfillment_mode)
    e = ctx.eligibility.get(channel)
    if e is None:
        if ctx.seller_type == SellerType.COMPANY.value:
            return None, []
        return f"{PF_NOT_HOMOLOGATED} Elegibilidade do canal para Pessoa Física não cadastrada.", []
    bad = [
        f"{ASPECTS[k]}: {e.aspects.get(k, {}).get('status', 'UNVERIFIED')}"
        for k in required_aspects(fulfillment_mode)
        if e.aspects.get(k, {}).get("status") not in OK
    ]
    if bad:
        prefix = PF_NOT_HOMOLOGATED if ctx.seller_type == SellerType.PERSON.value else "Elegibilidade não comprovada."
        return f"{prefix} Pendências: {'; '.join(bad)}", []
    conditional = any(e.aspects.get(k, {}).get("status") == "CONDITIONAL" for k in required_aspects(fulfillment_mode))
    if conditional and not e.conditions:
        return "Elegibilidade condicional sem condições verificáveis cadastradas: não autoriza operação real.", []
    violated = check_conditions(e.conditions, req)
    if violated:
        return f"Condições da elegibilidade não atendidas: {'; '.join(violated)}", []
    return None, list(e.restrictions)


def gate_text(code: str, base: dict[str, str], seller_type: str) -> dict[str, str]:
    return {**base, **GATE_TEXT.get(seller_type, {}).get(code, {})}


# ───────────────────────── estado inicial da matriz (fontes secundárias, nada comprovado) ─────────────────────────
_ACCESSED = "2026-10-25"
_SECONDARY = "fonte secundária (blog/guia), não oficial — confirmar na central do vendedor e com o contador"


def _all(status: str, note: str) -> dict[str, dict[str, str]]:
    return {k: {"status": status, "note": note} for k in ASPECTS}


SEED_ELIGIBILITY: list[dict[str, Any]] = [
    {
        "channel": "MERCADO_LIVRE",
        "aspects": {
            **_all("UNVERIFIED", "não confirmado em fonte oficial"),
            "registration": {
                "status": "UNVERIFIED",
                "note": "guias relatam cadastro de vendedor com CPF; confirmar na conta (fonte não oficial)",
            },
            "fiscal": {
                "status": "UNVERIFIED",
                "note": "fontes divergem sobre documento fiscal na venda por CPF; exige parecer profissional",
            },
        },
        "restrictions": ["fontes indicam limites para vendedor CPF (volume, reputação) — não confirmados"],
        "pending": [
            "confirmar na central do Mercado Livre o cadastro e a venda habitual por CPF",
            "parecer do contador sobre o documento fiscal da venda por Pessoa Física",
            "confirmar se a aplicação (API/OAuth) pode operar conta PF",
        ],
        # página oficial citada na issue #3 afirma cadastro e venda com CPF, mas devolveu HTTP 403 a este agente:
        # registrada como fonte a conferir; cadastro genérico ≠ API, venda à ordem e obrigações fiscais.
        "sources": [
            {
                "url": "https://vendedores.mercadolivre.com.br/nota/como-validar-passo-a-passo-a-identidade-da-sua-empresa",
                "kind": "fonte OFICIAL citada (cadastro/venda com CPF) — inacessível ao agente (HTTP 403); conferir",
                "accessed": "2026-10-26",
            },
            {
                "url": "https://agilize.com.br/artigos/vender-mercadolivre-cpf-cnpj",
                "kind": _SECONDARY,
                "accessed": _ACCESSED,
            },
        ],
        "scope": "conta de vendedor PF no Brasil; produtos nacionais; envio pelo canal ou pelo fornecedor",
    },
    {
        "channel": "SHOPEE",
        "aspects": _all("UNVERIFIED", "não confirmado em fonte oficial"),
        "restrictions": [
            "guias citam 'Loja Pessoal (CPF)' e limite de faturamento para migrar a CNPJ — não confirmado"
        ],
        "pending": [
            "confirmar na Central do Vendedor Shopee a loja pessoal e suas regras",
            "acesso de parceiro à API (Open Platform) para conta PF",
            "parecer do contador sobre obrigações fiscais",
        ],
        "sources": [
            {"url": "https://canaltech.com.br/apps/como-vender-na-shopee/", "kind": _SECONDARY, "accessed": _ACCESSED},
        ],
        "scope": "loja pessoal (CPF) no Brasil",
    },
    {
        "channel": "TIKTOK_SHOP",
        "aspects": {
            **_all("UNVERIFIED", "não confirmado em fonte oficial"),
            "registration": {
                "status": "UNSUPPORTED",
                "note": "política oficial 'Registro de Vendedores' (18/12/2025): 'apenas pessoas jurídicas (CNPJ) "
                "estão "
                "qualificadas para se registrar no TikTok Shop' — conferida em 2026-10-26",
            },
        },
        "restrictions": [
            "LIVE bloqueado para PF: a política oficial publicada exige CNPJ para cadastro de vendedor "
            "(não confundir com criador afiliado)"
        ],
        "pending": ["só reavaliar se o TikTok Shop Brasil publicar regra que admita vendedor Pessoa Física"],
        "sources": [
            {
                "url": "https://seller-br.tiktok.com/university/essay?identity=1&knowledge_id=3995530205677329",
                "kind": "fonte OFICIAL (TikTok Shop Seller University — Registro de Vendedores, 18/12/2025)",
                "accessed": "2026-10-26",
            },
            {
                "url": "https://canaltech.com.br/apps/como-vender-na-tiktok-shop/",
                "kind": _SECONDARY,
                "accessed": _ACCESSED,
            },
            {"url": "https://tactus.com.br/tiktok-shop-precisa-de-cnpj/", "kind": _SECONDARY, "accessed": _ACCESSED},
        ],
        "scope": "vendedor PF no Brasil",
    },
    {
        "channel": "WOOCOMMERCE",
        "aspects": {
            **_all("UNVERIFIED", "loja própria: depende do meio de pagamento e do modelo fiscal escolhidos"),
            "registration": {
                "status": "UNVERIFIED",
                "note": "loja própria não tem cadastro de vendedor; depende do "
                "prestador de pagamento aceitar Pessoa Física",
            },
        },
        "restrictions": [],
        "pending": [
            "escolher meio de pagamento que aceite Pessoa Física (elegibilidade do prestador)",
            "parecer do contador sobre o documento fiscal da venda pela loja própria",
        ],
        "sources": [],
        "scope": "loja própria (WooCommerce) com recebimento em nome da Pessoa Física",
    },
]


def seed_eligibility(db: Session) -> None:
    from aicommerce.models import ChannelEligibility, SellerProfile

    if db.get(SellerProfile, 1) is None:
        db.add(SellerProfile(id=1, person_type=SellerType.PERSON.value, restrictions=[]))
    for row in SEED_ELIGIBILITY:
        cur = db.get(ChannelEligibility, (row["channel"], SellerType.PERSON.value))
        if cur is not None and cur.updated_by == "seed" and cur.policy_version != ELIGIBILITY_POLICY_VERSION:
            # só linhas escritas pelo próprio seed (nunca o que o proprietário registrou com MFA); a versão sobe, então
            # aprovações antigas do gate do canal deixam de valer (impressão do perfil inclui a versão)
            for k, v in row.items():
                setattr(cur, k, v)
            cur.status = overall(row["aspects"])
            cur.policy_version, cur.version = ELIGIBILITY_POLICY_VERSION, (cur.version or 0) + 1
            continue
        if cur is None:
            db.add(
                ChannelEligibility(
                    seller_type=SellerType.PERSON.value,
                    status=overall(row["aspects"]),
                    policy_version=ELIGIBILITY_POLICY_VERSION,
                    verified_at=None,
                    updated_by="seed",
                    **row,
                )
            )


# ───────────────────────────────────────── serviço ─────────────────────────────────────────
class SellerError(Exception):
    http = 409


def get_profile(db: Session, lock: bool = False):
    from aicommerce.models import SellerProfile

    p = db.get(SellerProfile, 1, with_for_update=lock)
    if p is None:  # banco anterior ao seed: perfil padrão do proprietário
        p = SellerProfile(id=1, person_type=SellerType.PERSON.value, restrictions=[])
        db.add(p)
        db.flush()
    return p


def context(db: Session) -> SellerContext:
    from aicommerce.models import ChannelEligibility

    p = get_profile(db)
    st = p.person_type
    rows = db.execute(select(ChannelEligibility).where(ChannelEligibility.seller_type == st)).scalars()
    elig = {
        r.channel: EligibilitySnapshot(
            r.channel, r.seller_type, r.status, r.aspects, r.restrictions, r.pending, r.conditions or [], r.version
        )
        for r in rows
    }
    return SellerContext(st, elig, fingerprints(_material(p), elig))


def _material(p: Any) -> dict[str, Any]:
    return {
        "person_type": p.person_type,
        "uf": p.uf,
        "fiscal_model": p.fiscal_model,
        "restrictions": sorted(p.restrictions or []),
    }


def _expire_gates(db: Session, codes: tuple[str, ...] | list[str], *, actor: str, reason: str) -> list[str]:
    from aicommerce.governance.gates import APPROVED, _lose_approval
    from aicommerce.models import CertificationGate

    lost = []
    for code in codes:
        g = db.get(CertificationGate, code, with_for_update=True)
        if g is not None and g.state in APPROVED:
            _lose_approval(db, g, state="EXPIRED", actor=actor, reason=reason)
            lost.append(code)
    return lost


def update_profile(db: Session, *, actor: str, mfa: bool, changes: dict[str, Any]) -> tuple[Any, list[str]]:
    """Muda o perfil (só proprietário com MFA). Trocar PF↔PJ derruba as aprovações dependentes do perfil."""
    if not mfa:
        raise SellerError("mudar o perfil do vendedor exige sessão com MFA")
    p = get_profile(db, lock=True)
    if "person_type" in changes and changes["person_type"] not in SellerType.__members__:
        raise SellerError("tipo de vendedor inválido (PERSON ou COMPANY)")
    if "fiscal_model" in changes and changes["fiscal_model"] not in FISCAL_MODELS:
        raise SellerError(f"modelo fiscal inválido; use {sorted(FISCAL_MODELS)}")
    before = {"person_type": p.person_type, "uf": p.uf, "fiscal_model": p.fiscal_model}
    old = _material(p)
    new = {**old, **{k: changes[k] for k in ("person_type", "uf", "fiscal_model") if k in changes}}
    if "restrictions" in changes:
        new["restrictions"] = sorted(changes["restrictions"] or [])
    changed = sorted(k for k in old if old[k] != new[k])
    # só as homologações que consideram alguma propriedade alterada perdem a aprovação (mudança irrelevante não)
    affected = [code for code, keys in MATERIAL_FIELDS.items() if set(keys) & set(changed)]
    lost: list[str] = []
    if affected:
        lost = _expire_gates(
            db,
            affected,
            actor=actor,
            reason=f"perfil do vendedor mudou ({', '.join(changed)}): a homologação depende disso, requer nova revisão",
        )
    for k in ("person_type", "uf", "fiscal_model", "restrictions"):
        if k in changes:
            setattr(p, k, changes[k])
    p.version += 1
    p.updated_by, p.updated_at = actor, dt.datetime.now(dt.UTC)
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="seller.profile.updated",
        subject_type="seller_profile",
        subject_id="1",
        payload={
            "before": before,
            "after": {k: changes[k] for k in changes if k != "restrictions"},
            "changed": changed,
            "gates_lost": lost,
        },
    )
    return p, lost


def update_eligibility(
    db: Session,
    channel: str,
    seller_type: str,
    *,
    actor: str,
    mfa: bool,
    aspects: dict[str, dict[str, str]],
    restrictions: list[str],
    pending: list[str],
    sources: list[dict[str, str]],
    scope: str,
    conditions: list[dict[str, Any]] | None = None,
) -> tuple[Any, list[str]]:
    """Registra a verificação de um canal (proprietário com MFA). Promover aspecto a SUPPORTED/CONDITIONAL exige nota
    e fonte; CONDITIONAL exige restrições. Qualquer mudança derruba a aprovação do gate do canal (nova revisão)."""
    from aicommerce.governance.golive import CHANNEL_GATE, Channel
    from aicommerce.models import ChannelEligibility

    if not mfa:
        raise SellerError("registrar elegibilidade exige sessão com MFA")
    if channel not in Channel.__members__ or seller_type not in SellerType.__members__:
        raise SellerError("canal ou tipo de vendedor inválido")
    if set(aspects) != set(ASPECTS):
        raise SellerError(f"informe todos os aspectos: {sorted(ASPECTS)}")
    for k, a in aspects.items():
        if a.get("status") not in Eligibility.__members__:
            raise SellerError(f"{k}: estado inválido")
        if a["status"] in OK and (len(str(a.get("note", "")).strip()) < 10 or not sources):
            raise SellerError(f"{k}: elegibilidade comprovada exige nota (mín. 10) e fonte registrada")
    conditions = conditions or []
    for c in conditions:
        if c.get("field") not in CONDITION_FIELDS or not isinstance(c.get("allowed"), list) or not c["allowed"]:
            raise SellerError(f"condição inválida; campos verificáveis: {sorted(CONDITION_FIELDS)} com lista 'allowed'")
    if any(a["status"] == "CONDITIONAL" for a in aspects.values()):
        if not restrictions:
            raise SellerError("elegibilidade condicional exige descrever as restrições")
        if not conditions:
            raise SellerError("elegibilidade condicional exige condições verificáveis (ex.: ufs, fulfillment_modes)")
    if any(not str(s.get("url", "")).startswith("https://") for s in sources):
        raise SellerError("fonte precisa de endereço https://")
    row = db.get(ChannelEligibility, (channel, seller_type), with_for_update=True)
    before = row.status if row else None
    if row is None:
        row = ChannelEligibility(
            channel=channel, seller_type=seller_type, version=0, policy_version=ELIGIBILITY_POLICY_VERSION
        )
        db.add(row)
    row.aspects, row.restrictions, row.pending, row.sources, row.scope = aspects, restrictions, pending, sources, scope
    row.conditions = conditions
    row.status = overall(aspects)
    row.version = (row.version or 0) + 1
    row.policy_version = ELIGIBILITY_POLICY_VERSION
    row.verified_at, row.updated_by, row.updated_at = dt.datetime.now(dt.UTC), actor, dt.datetime.now(dt.UTC)
    lost: list[str] = []
    if seller_type == get_profile(db).person_type:
        lost = _expire_gates(
            db,
            [CHANNEL_GATE[Channel(channel)]],
            actor=actor,
            reason=f"elegibilidade de {channel} para {seller_type} mudou ({before} → {row.status}): nova revisão",
        )
    audit.record(
        db,
        actor_type="user",
        actor_id=actor,
        action="seller.eligibility.updated",
        subject_type="channel",
        subject_id=channel,
        payload={
            "seller_type": seller_type,
            "from": before,
            "to": row.status,
            "version": row.version,
            "conditions": conditions,
            "sources": [s.get("url") for s in sources],
            "gates_lost": lost,
        },
    )
    return row, lost


def profile_out(db: Session) -> dict[str, Any]:
    from aicommerce.models import CertificationGate

    p = get_profile(db)
    leg = db.get(CertificationGate, "LEG-01")
    return {
        "person_type": p.person_type,
        "person_type_label": "Pessoa Física (CPF)" if p.person_type == "PERSON" else "Pessoa Jurídica (CNPJ)",
        "country": p.country,
        "uf": p.uf,
        "tax_id_status": p.tax_id_status,
        "tax_id_note": "O identificador fiscal não é armazenado pelo sistema (não é necessário hoje).",
        "fiscal_model": p.fiscal_model,
        "fiscal_model_label": FISCAL_MODELS.get(p.fiscal_model, p.fiscal_model),
        "legal_review": leg.state if leg else "NOT_STARTED",
        "restrictions": p.restrictions,
        "verified_at": p.verified_at.isoformat() if p.verified_at else None,
        "verified_source": p.verified_source,
        "version": p.version,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "fiscal_models": FISCAL_MODELS,
        "note": "Perfil pretendido. CPF não é autorização fiscal nem comercial: venda real exige elegibilidade "
        "comprovada por canal e homologações aprovadas para este perfil.",
    }


def eligibility_out(db: Session, seller_type: str | None = None) -> list[dict[str, Any]]:
    from aicommerce.governance.golive import Channel
    from aicommerce.models import ChannelEligibility

    st = seller_type or get_profile(db).person_type
    rows = {
        r.channel: r
        for r in db.execute(select(ChannelEligibility).where(ChannelEligibility.seller_type == st)).scalars()
    }
    out = []
    for ch in Channel:
        r = rows.get(ch.value)
        if r is None:
            out.append(
                {
                    "channel": ch.value,
                    "seller_type": st,
                    "status": "UNVERIFIED" if st == "PERSON" else None,
                    "registered": False,
                    "aspects": {},
                    "restrictions": [],
                    "pending": [],
                    "sources": [],
                    "note": "sem verificação cadastrada"
                    + ("" if st == "PERSON" else " (PJ: conta apta comprovada pelo gate do canal)"),
                }
            )
            continue
        out.append(
            {
                "channel": r.channel,
                "seller_type": r.seller_type,
                "status": r.status,
                "registered": True,
                "aspects": {k: {**r.aspects.get(k, {}), "label": ASPECTS[k]} for k in ASPECTS},
                "restrictions": r.restrictions,
                "pending": r.pending,
                "sources": r.sources,
                "scope": r.scope,
                "conditions": r.conditions or [],
                "policy_version": r.policy_version,
                "version": r.version,
                "verified_at": r.verified_at.isoformat() if r.verified_at else None,
                "live_possible": r.status in OK,
                "note": "Disponível só em simulação até a elegibilidade e as homologações serem comprovadas."
                if r.status not in OK
                else "Elegibilidade comprovada — ainda exige as homologações (gates) aprovadas.",
            }
        )
    return out
