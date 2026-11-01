"use client";
// Clara: gates de go-live (evidências → revisão; a decisão é do owner com MFA na API).
// Orion: dashboard de vendas por canal. Uma série por gráfico, cor validada, rótulos diretos, tabela alternativa.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type GateDetail, type GateRow, type SalesDashboard } from "@/lib/api";
import { ML_PERSON_ELIGIBILITY, suggestedDecision } from "@/lib/pilot";
import { barPct, formatBps, formatMinor, GATE_STATE } from "@/lib/format";

const msg = (e: unknown) => (e instanceof ApiError ? `${e.status}: ${e.message}` : "Falha de conexão com a API");

export function GatesPanel({ token, canManage, canApprove = false, refreshSignal }: {
  token: string; canManage: boolean; canApprove?: boolean; refreshSignal: number;
}) {
  const [rows, setRows] = useState<GateRow[]>([]);
  const [open, setOpen] = useState<GateDetail | null>(null);
  const [ref, setRef] = useState("");
  const [kind, setKind] = useState("DOCUMENTO");
  const [error, setError] = useState<string | null>(null);
  const [dec, setDec] = useState(suggestedDecision(""));
  const [elig, setElig] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setRows((await api.gates(token)).items);
      const ml = (await api.eligibility(token)).items.find((i) => i.channel === "MERCADO_LIVRE");
      setElig(ml?.status ?? null);
    } catch (e) { setError(msg(e)); }
  }, [token]);
  useEffect(() => { if (open) setDec(suggestedDecision(open.code)); }, [open?.code]);

  async function decide(e: FormEvent) {
    e.preventDefault();
    if (!open) return;
    const restrictions = dec.restrictionsText.split(";").map((x) => x.trim()).filter(Boolean);
    const scope = dec.decision === "REJECTED" ? {} : { ...dec.scope, ...(restrictions.length ? { restrictions } : {}) };
    await act(() => api.gateDecide(token, open.code, { decision: dec.decision, scope, valid_until: dec.valid_until, note: dec.note }));
  }
  async function confirmEligibility() {
    setError(null);
    try { await api.updateEligibility(token, "MERCADO_LIVRE", ML_PERSON_ELIGIBILITY); void load(); }
    catch (e) { setError(msg(e)); }
  }
  useEffect(() => { void load(); }, [load, refreshSignal]);

  async function act(fn: () => Promise<GateDetail>) {
    setError(null);
    try { setOpen(await fn()); void load(); } catch (e) { setError(msg(e)); }
  }
  async function addEvidence(e: FormEvent) {
    e.preventDefault();
    if (!open) return;
    await act(() => api.gateEvidence(token, open.code, kind, ref, "registrado pelo painel da Clara"));
    setRef("");
  }

  if (open) return (
    <div className="dept">
      <button type="button" className="link" onClick={() => setOpen(null)}>← Gates</button>
      <h3>{open.code} · {open.title}</h3>
      <div className="small">{GATE_STATE[open.state] ?? open.state} · responsável: {open.responsible}</div>
      <div className="muted small">Evidência esperada: {open.expected_evidence}</div>
      <ul className="list">{open.evidence.map((e) => (
        <li key={e.id}>{e.kind}: <code>{e.reference}</code> <span className="muted small">sha {e.sha256.slice(0, 10)}…</span></li>))}
        {open.evidence.length === 0 && <li className="muted small">Nenhuma evidência registrada.</li>}</ul>
      {canManage && !["APPROVED", "APPROVED_WITH_RESTRICTIONS"].includes(open.state) && (
        <form className="stack" onSubmit={addEvidence}>
          <select aria-label="Tipo de evidência" value={kind} onChange={(e) => setKind(e.target.value)}>
            {["DOCUMENTO", "PARECER", "ACEITE_RISCO", "RELATORIO_TESTE", "CONFIRMACAO_CONTA", "CONTRATO", "CERTIFICADO", "OUTRO"]
              .map((k) => <option key={k}>{k}</option>)}
          </select>
          <input aria-label="Referência do documento" required minLength={3} value={ref} onChange={(e) => setRef(e.target.value)}
            placeholder="onde o documento está guardado" />
          <button type="submit">Registrar evidência</button>
        </form>
      )}
      {canManage && open.state === "COLLECTING_EVIDENCE" && (
        <button type="button" className="primary" onClick={() => act(() => api.gateSubmit(token, open.code))}>
          Enviar para revisão</button>)}
      {canApprove && open.state === "IN_REVIEW" && (
        <form className="stack" onSubmit={decide} aria-label="Decisão do proprietário">
          <h4>Sua decisão</h4>
          <select aria-label="Decisão" value={dec.decision}
            onChange={(e) => setDec({ ...dec, decision: e.target.value as typeof dec.decision })}>
            <option value="APPROVED">Aprovar</option>
            <option value="APPROVED_WITH_RESTRICTIONS">Aprovar com restrições</option>
            <option value="REJECTED">Rejeitar</option>
          </select>
          {dec.decision === "APPROVED_WITH_RESTRICTIONS" && (
            <label className="small">Restrições (separe com ;)
              <textarea aria-label="Restrições" required value={dec.restrictionsText}
                onChange={(e) => setDec({ ...dec, restrictionsText: e.target.value })} /></label>)}
          <label className="small">Justificativa
            <textarea aria-label="Justificativa" required minLength={10} value={dec.note}
              onChange={(e) => setDec({ ...dec, note: e.target.value })} /></label>
          <span className="muted small">Vale até {new Date(dec.valid_until ?? "").toLocaleDateString("pt-BR")} · escopo:
            Mercado Livre, PR, dropship do fornecedor</span>
          <button type="submit" className="primary">Confirmar decisão</button>
        </form>
      )}
      <p className="muted small">A aprovação é decisão humana do proprietário (com MFA) sobre evidência real. Em
        DRY_RUN isso não habilita nenhuma operação: o modo LIVE é que consulta os gates.</p>
      {error && <p className="err small" role="alert">{error}</p>}
    </div>
  );

  return (
    <div className="dept">
      <h3>Gates de go-live</h3>
      {canApprove && elig !== "CONDITIONAL" && elig !== "SUPPORTED" && (
        <div className="card stack">
          <strong>Elegibilidade do Mercado Livre para Pessoa Física: não confirmada</strong>
          <span className="muted small">Verificado na sua conta: cadastro CPF ativo, OAuth funcionando, Mercado Envios ativo,
            Dropi conectado. Fica condicional: venda habitual, venda à ordem e fiscal (aceite de risco seu), só no piloto
            (PR, dropship). Confirme antes de aprovar o CH-ML.</span>
          <button type="button" className="primary" onClick={() => void confirmEligibility()}>Confirmar elegibilidade</button>
        </div>
      )}
      {elig && (elig === "CONDITIONAL" || elig === "SUPPORTED") && (
        <p className="small">Mercado Livre para Pessoa Física: elegibilidade {elig === "SUPPORTED" ? "comprovada" : "condicional (piloto)"}.</p>)}
      <ul className="inbox">{rows.map((g) => (
        <li key={g.code}><button type="button" className="inbox-item" onClick={() => act(() => api.gate(token, g.code))}>
          <span className="inbox-title">{g.code} · {g.title}</span>
          <span className="inbox-meta"><span className={`status s-${g.state === "APPROVED" ? "SUCCEEDED" : g.state}`}>
            {GATE_STATE[g.state] ?? g.state}</span><span className="muted">bloqueia: {g.blocks}</span></span>
        </button></li>))}</ul>
      {error && <p className="err small" role="alert">{error}</p>}
    </div>
  );
}

export function SalesPanel({ token, refreshSignal }: { token: string; refreshSignal: number }) {
  const [d, setD] = useState<SalesDashboard | null>(null);
  const [days, setDays] = useState(30);
  const [table, setTable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.salesDashboard(token, days).then(setD).catch((e) => setError(msg(e))); }, [token, days, refreshSignal]);
  if (!d) return error ? <p className="err small">{error}</p> : null;
  const maxCh = Math.max(0, ...d.channels.map((c) => c.net_revenue_minor));
  const maxDay = Math.max(0, ...d.daily.map((x) => x.revenue_minor));
  return (
    <div className="dept sales">
      <div className="row between">
        <h3>Vendas por canal {d.simulated && <span className="sim">Simulado</span>}</h3>
        <select aria-label="Período" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>7 dias</option><option value={30}>30 dias</option><option value={90}>90 dias</option>
        </select>
      </div>
      <div className="kpis">
        <div><span className="muted small">Pedidos</span><strong>{d.totals.orders}</strong></div>
        <div><span className="muted small">Receita líquida</span><strong>{formatMinor(d.totals.net_revenue_minor)}</strong></div>
        <div><span className="muted small">Contribuição est.</span><strong>{formatMinor(d.totals.contribution_minor)}</strong></div>
      </div>
      {d.channels.length === 0 ? <p className="muted small">Sem vendas no período.</p> : (
        <ul className="hbars" aria-label="Receita líquida por canal">
          {d.channels.map((c) => (
            <li key={c.channel} title={`${c.channel}: ${formatMinor(c.net_revenue_minor)} · ${c.orders} pedidos · margem ${formatBps(c.margin_bps)}`}>
              <span className="hb-label">{c.channel}</span>
              <span className="hb-track"><span className="hb-bar" style={{ width: `${barPct(c.net_revenue_minor, maxCh)}%` }} /></span>
              <span className="hb-value">{formatMinor(c.net_revenue_minor)}</span>
            </li>))}
        </ul>
      )}
      <div className="row between">
        <h4>Receita bruta diária <span className="muted small">(inclui pedidos reembolsados)</span></h4>
        <button type="button" className="link" onClick={() => setTable((v) => !v)}>{table ? "Ver gráfico" : "Ver tabela"}</button>
      </div>
      {table ? (
        <table className="data"><thead><tr><th>Data</th><th>Receita</th></tr></thead>
          <tbody>{d.daily.filter((x) => x.revenue_minor > 0).map((x) => (
            <tr key={x.date}><td>{new Date(x.date + "T12:00:00Z").toLocaleDateString("pt-BR")}</td>
              <td>{formatMinor(x.revenue_minor)}</td></tr>))}</tbody></table>
      ) : (
        <div className="cols" role="img" aria-label={`Receita bruta diária nos últimos ${days} dias; máximo ${formatMinor(maxDay)}`}>
          {d.daily.map((x) => (
            <span key={x.date} className="col" tabIndex={x.revenue_minor > 0 ? 0 : -1}
              title={`${new Date(x.date + "T12:00:00Z").toLocaleDateString("pt-BR")}: ${formatMinor(x.revenue_minor)}`}>
              <span className="col-bar" style={{ height: `${barPct(x.revenue_minor, maxDay)}%` }} />
            </span>))}
        </div>
      )}
      <p className="muted small">{d.note}</p>
    </div>
  );
}
