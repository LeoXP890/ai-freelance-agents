"use client";
// UX-09 Homologações (UXD-014): checklist de go-live por empresa, fiscal, canal, fornecedor e consumidor. Evidência é
// registrada com referência e hash; enviar para revisão é da operação, APROVAR é decisão humana do proprietário com
// MFA na API — nunca um clique local. Em DRY_RUN nada disso libera operação real.
import { CheckCircle2, CircleSlash, Clock, FilePlus2, Send, ShieldCheck, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, type GateDecision, type GateDetail, type GateRow } from "@/lib/api";
import { ML_PERSON_ELIGIBILITY, suggestedDecision } from "@/lib/pilot";
import { GATE_STATE } from "@/lib/format";
import { SELLER_SHORT } from "@/lib/seller";
import { useDesk } from "../context";
import { Skeleton } from "../table";
import { Empty } from "../ui";

const TONE: Record<string, string> = { NOT_STARTED: "", COLLECTING_EVIDENCE: "info", IN_REVIEW: "warn", APPROVED: "ok", APPROVED_WITH_RESTRICTIONS: "ok", REJECTED: "crit", EXPIRED: "crit" };
const GROUP: Record<string, string> = { CH: "Canais de venda", LEG: "Elegibilidade do vendedor", OPS: "Consumidor e LGPD", SUP: "Fornecedor", TAX: "Fiscal" };
const EVIDENCE = ["DOCUMENTO", "PARECER", "ACEITE_RISCO", "RELATORIO_TESTE", "CONFIRMACAO_CONTA", "CONTRATO", "CERTIFICADO", "OUTRO"];
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export default function Gates() {
  const desk = useDesk();
  const [rows, setRows] = useState<GateRow[] | null>(null);
  const [sellerType, setSellerType] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mlElig, setMlElig] = useState<string | null | undefined>(undefined);
  const load = useCallback(() => {
    api.gates(desk.token).then((r) => { setRows(r.items); setSellerType(r.seller_type ?? null); setError(null); }).catch((e) => setError(msg(e)));
    api.eligibility(desk.token).then((r) => setMlElig(r.items.find((i) => i.channel === "MERCADO_LIVRE")?.status ?? null)).catch(() => setMlElig(undefined));
  }, [desk.token]);
  async function confirmEligibility() {
    setError(null);
    try { await api.updateEligibility(desk.token, "MERCADO_LIVRE", ML_PERSON_ELIGIBILITY); desk.notify("Elegibilidade do Mercado Livre registrada."); load(); }
    catch (e) { setError(msg(e)); }
  }
  const eligOk = mlElig === "SUPPORTED" || mlElig === "CONDITIONAL";
  useEffect(() => { if (desk.me) load(); }, [load, desk.refresh, desk.me]);
  const approved = rows?.filter((g) => g.state === "APPROVED" || g.state === "APPROVED_WITH_RESTRICTIONS").length ?? 0;
  const groups = [...new Set((rows ?? []).map((g) => g.code.split("-")[0]!))];
  return (
    <div className="page split-page">
      <div className="page-head"><div><h1>Homologações</h1><p>O que precisa estar comprovado antes de qualquer operação real. {rows ? `${approved} de ${rows.length} aprovados.` : ""}</p></div></div>
      <div className="callout info" role="note" style={{ marginBottom: 16 }}><ShieldCheck size={16} aria-hidden="true" /> Aprovar um gate é decisão do proprietário (com MFA) sobre evidência real. Esta tela registra evidências e envia para revisão; nada é marcado como concluído localmente.
        {sellerType && <> Exigências apresentadas para o perfil <strong>{SELLER_SHORT[sellerType] ?? sellerType}</strong>{sellerType === "PERSON" ? " — os gates fiscais exigem parecer profissional ou o seu aceite de risco, e a elegibilidade do canal confirmada." : "."}</>}</div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      {sellerType === "PERSON" && mlElig !== undefined && desk.can("gates:approve") && (
        <section className="dk-card" aria-label="Elegibilidade do Mercado Livre" style={{ marginBottom: 16, padding: 16 }}>
          {eligOk ? (
            <div className="row-item"><span className="dk-chip ok"><CheckCircle2 aria-hidden="true" />Mercado Livre · Pessoa Física</span>
              <span className="grow">Elegibilidade {mlElig === "SUPPORTED" ? "comprovada" : "condicional (piloto: PR, dropship pelo Dropi)"}.</span></div>
          ) : (
            <>
              <h3 className="sec-title" style={{ marginTop: 0 }}>Passo 1 · Elegibilidade do Mercado Livre para Pessoa Física: não confirmada</h3>
              <p className="muted" style={{ margin: "0 0 10px" }}>Verificado na sua conta: cadastro CPF ativo, OAuth funcionando, Mercado Envios ativo e Dropi
                conectado. Fica condicional, só no piloto (PR, dropship): venda habitual, venda à ordem e fiscal (aceite de risco seu).
                Confirme antes de aprovar o CH-ML.</p>
              <button type="button" className="btn primary" disabled={!desk.online} onClick={() => void confirmEligibility()}><ShieldCheck />Confirmar elegibilidade</button>
            </>
          )}
        </section>
      )}
      <div className={`split ${open ? "with-detail" : ""}`}>
        <section className="dk-card table-card" aria-label="Gates de go-live">
          {rows === null ? <Skeleton /> : rows.length === 0 ? <Empty icon={<ShieldCheck />} title="Nenhum gate cadastrado" /> : (
            <table className="dk-table">
              <thead><tr><th>Gate</th><th>Situação</th><th className="opt">Bloqueia</th><th className="opt">Responsável</th></tr></thead>
              {groups.map((gr) => (
                <tbody key={gr}>
                  <tr className="static group-row"><td colSpan={4}>{GROUP[gr] ?? gr}</td></tr>
                  {rows.filter((g) => g.code.startsWith(`${gr}-`)).map((g) => (
                    <tr key={g.code} aria-selected={open === g.code} onClick={() => setOpen(g.code)}>
                      <td><button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); setOpen(g.code); }} aria-label={`Abrir gate ${g.code}`}>{g.title}</button><div className="faint">{g.code}{g.valid_until ? ` · válido até ${new Date(g.valid_until).toLocaleDateString("pt-BR")}` : ""}</div></td>
                      <td><span className={`dk-chip ${TONE[g.state] ?? ""}`}>{g.state.startsWith("APPROVED") ? <CheckCircle2 aria-hidden="true" /> : g.state === "IN_REVIEW" ? <Clock aria-hidden="true" /> : <CircleSlash aria-hidden="true" />}{GATE_STATE[g.state] ?? g.state}</span></td>
                      <td className="opt faint">{g.blocks}</td>
                      <td className="opt faint">{g.responsible}</td>
                    </tr>))}
                </tbody>
              ))}
            </table>
          )}
        </section>
        {open && <GatePane code={open} onClose={() => setOpen(null)} onChanged={load} />}
      </div>
    </div>
  );
}

function GatePane({ code, onClose, onChanged }: { code: string; onClose(): void; onChanged(): void }) {
  const desk = useDesk();
  const [g, setG] = useState<GateDetail | null>(null);
  const [kind, setKind] = useState("DOCUMENTO");
  const [ref, setRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const manage = desk.can("gates:manage");
  useEffect(() => { setG(null); api.gate(desk.token, code).then(setG).catch((e) => setError(msg(e))); }, [desk.token, code]);
  async function act(fn: () => Promise<GateDetail>, ok: string) {
    setError(null);
    try { setG(await fn()); desk.notify(ok); onChanged(); } catch (e) { setError(msg(e)); }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    await act(() => api.gateEvidence(desk.token, code, kind, ref, "registrado pelo app desktop"), "Evidência registrada com hash.");
    setRef("");
  }
  const locked = g && ["APPROVED", "APPROVED_WITH_RESTRICTIONS"].includes(g.state);
  const [dec, setDec] = useState(() => suggestedDecision(code));
  useEffect(() => setDec(suggestedDecision(code)), [code]);
  async function decide(e: FormEvent) {
    e.preventDefault();
    const restrictions = dec.restrictionsText.split(";").map((x) => x.trim()).filter(Boolean);
    const scope = dec.decision === "REJECTED" ? {} : { ...dec.scope, ...(dec.decision === "APPROVED_WITH_RESTRICTIONS" && restrictions.length ? { restrictions } : {}) };
    const body: GateDecision = { decision: dec.decision, scope, valid_until: dec.valid_until, note: dec.note };
    await act(() => api.gateDecide(desk.token, code, body), dec.decision === "REJECTED" ? "Gate rejeitado." : "Decisão registrada.");
  }
  return (
    <aside className="dk-card detail-pane" aria-label="Detalhe do gate">
      <div className="detail-head"><div style={{ minWidth: 0 }}><div className="faint">{code}{g ? ` · responsável: ${g.responsible}` : ""}</div><h2 className="detail-title">{g?.title ?? "Carregando…"}</h2></div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar gate"><X size={18} /></button></div>
      {error && <p role="alert" className="dk-error" style={{ padding: "8px 18px 0" }}>{error}</p>}
      {g && (
        <div className="detail-body">
          <div className="chip-row"><span className={`dk-chip ${TONE[g.state] ?? ""}`}>{GATE_STATE[g.state] ?? g.state}</span>{g.approved_by && <span className="dk-chip ok">aprovado por {g.approved_by}</span>}</div>
          <dl className="dk-kv"><dt>Bloqueia</dt><dd>{g.blocks}</dd><dt>Evidência esperada</dt><dd>{g.expected_evidence}</dd>
            {g.valid_until && <><dt>Válido até</dt><dd>{new Date(g.valid_until).toLocaleDateString("pt-BR")}</dd></>}
            {Array.isArray(g.scope?.seller_types) && <><dt>Aprovado para</dt><dd>{(g.scope.seller_types as string[]).map((t) => SELLER_SHORT[t] ?? t).join(", ")}</dd></>}
            {g.scope?.restrictions ? <><dt>Restrições</dt><dd>{String(g.scope.restrictions)}</dd></> : null}</dl>
          {g.requirements && (
            <section aria-label="Requisitos do gate">
              <h3 className="sec-title">Requisitos {g.seller_type ? `(${SELLER_SHORT[g.seller_type] ?? g.seller_type})` : ""}</h3>
              <ul className="req-list">{g.requirements.map((r) => (
                <li key={r.label} className={r.met ? "ok" : "no"}>{r.met ? <CheckCircle2 size={14} aria-hidden="true" /> : <CircleSlash size={14} aria-hidden="true" />}
                  <span>{r.label}</span><span className="faint">{r.met ? "cumprido" : "pendente — bloqueia a operação real"}</span></li>))}</ul>
            </section>
          )}
          <section aria-label="Evidências registradas">
            <h3 className="sec-title">Evidências ({g.evidence.length})</h3>
            {g.evidence.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nenhuma evidência registrada.</p> : (
              <div className="rows">{g.evidence.map((e) => (
                <div key={e.id} className="row-item"><span className="grow"><span className="t">{e.kind}</span><span className="meta ellipsis" style={{ display: "block" }}>{e.reference}</span></span>
                  <span className="faint num" title={e.sha256}>{e.sha256.slice(0, 10)}… · {new Date(e.submitted_at).toLocaleDateString("pt-BR")}</span></div>))}</div>
            )}
          </section>
          {manage && !locked && (
            <form className="row-actions" onSubmit={add}>
              <select className="field" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Tipo de evidência">{EVIDENCE.map((k) => <option key={k}>{k}</option>)}</select>
              <input className="field" style={{ flex: 1, minWidth: 160 }} required minLength={3} value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Onde o documento está guardado" aria-label="Referência do documento" />
              <button type="submit" className="btn" disabled={!desk.online}><FilePlus2 />Registrar</button>
            </form>
          )}
          {desk.can("gates:approve") && g.state === "IN_REVIEW" && (
            <form onSubmit={decide} aria-label="Sua decisão" style={{ display: "grid", gap: 8 }}>
              <h3 className="sec-title">Sua decisão</h3>
              <select className="field" aria-label="Decisão" value={dec.decision}
                onChange={(e) => setDec({ ...dec, decision: e.target.value as GateDecision["decision"] })}>
                <option value="APPROVED">Aprovar</option>
                <option value="APPROVED_WITH_RESTRICTIONS">Aprovar com restrições</option>
                <option value="REJECTED">Rejeitar</option>
              </select>
              {dec.decision === "APPROVED_WITH_RESTRICTIONS" && (
                <label className="faint">Restrições (separe com ;)
                  <textarea className="field" rows={3} required aria-label="Restrições" value={dec.restrictionsText}
                    onChange={(e) => setDec({ ...dec, restrictionsText: e.target.value })} style={{ width: "100%" }} /></label>)}
              <label className="faint">Justificativa
                <textarea className="field" rows={2} required minLength={10} aria-label="Justificativa" value={dec.note}
                  onChange={(e) => setDec({ ...dec, note: e.target.value })} style={{ width: "100%" }} /></label>
              <span className="faint">Vale até {new Date(dec.valid_until ?? "").toLocaleDateString("pt-BR")} · escopo: Mercado Livre, PR, dropship do fornecedor</span>
              <button type="submit" className="btn primary" disabled={!desk.online}><CheckCircle2 />Confirmar decisão</button>
            </form>
          )}
          <p className="faint" style={{ margin: 0 }}>Em DRY_RUN nenhum gate libera operação real; o modo LIVE é que consulta os gates antes de executar.</p>
        </div>
      )}
      {g && manage && g.state === "COLLECTING_EVIDENCE" && (
        <div className="detail-foot"><button type="button" className="btn primary" disabled={!desk.online || g.evidence.length === 0} onClick={() => act(() => api.gateSubmit(desk.token, code), "Enviado para revisão do proprietário.")}><Send />Enviar para revisão</button></div>
      )}
    </aside>
  );
}
