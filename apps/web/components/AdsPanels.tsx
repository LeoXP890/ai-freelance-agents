"use client";
// Mídia (COM-008): campanhas do Pixel e analytics de atribuição do Orion. Valores em centavos/bps, sem float.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type CampaignRow, type StopReport } from "@/lib/api";
import { barPct, formatBps, formatMinor } from "@/lib/format";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");
const STATUS: Record<string, string> = {
  ACTIVE: "Ativa", PAUSE_REQUESTED: "Pausa solicitada", PAUSED: "Pausada", ENDED: "Teto atingido", ERROR: "Pausa falhou",
};
const roas = (x: number | null) => (x === null ? "—" : `${(x / 100).toFixed(2).replace(".", ",")}×`);

function useCampaigns(token: string, refreshSignal: number) {
  const [items, setItems] = useState<CampaignRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setItems((await api.campaigns(token)).items); } catch (e) { setError(msg(e)); }
  }, [token]);
  useEffect(() => { void load(); }, [load, refreshSignal]);
  return { items, error, setError, load };
}

export function CampaignsPanel({ token, perms, refreshSignal, onProposed }: {
  token: string; perms: string[]; refreshSignal: number; onProposed(): void;
}) {
  const { items, error, setError, load } = useCampaigns(token, refreshSignal);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, proposed = false) => {
    setBusy(true); setError(null);
    try { await fn(); await load(); if (proposed) onProposed(); } catch (e) { setError(msg(e)); } finally { setBusy(false); }
  };
  if (!items) return error ? <p className="err small">{error}</p> : null;
  return (
    <div className="dept">
      <h3>Campanhas</h3>
      {error && <p className="err small" role="alert">{error}</p>}
      {items.length === 0 ? <p className="muted small">Nenhuma campanha criada (crie via proposta aprovada).</p> : (
        <ul className="list">
          {items.map((c) => (
            <li key={c.id} className="campaign">
              <div className="row between">
                <strong>{c.name}</strong>
                <span className={`chip st-${c.status.toLowerCase()}`}>{STATUS[c.status] ?? c.status}</span>
              </div>
              <div className="small">
                Gasto {formatMinor(c.spend_minor)} de {formatMinor(c.cap_minor)} ({formatBps(c.cap_used_bps)})
                {c.simulated && <span className="sim"> Simulado</span>}
              </div>
              <span className="hb-track" role="img" aria-label={`Teto usado: ${formatBps(c.cap_used_bps)}`}>
                <span className={`hb-bar ${c.cap_used_bps > 10_000 ? "over" : ""}`}
                  style={{ width: `${Math.min(100, c.cap_used_bps / 100)}%` }} />
              </span>
              <div className="small muted">
                CTR {c.ctr_bps === null ? "—" : formatBps(c.ctr_bps)} · CPC {c.cpc_minor === null ? "—" : formatMinor(c.cpc_minor)}
                · ROAS {roas(c.roas_x100)} · {c.reconciled ? "conciliado com o ledger" : <strong className="warn">divergente do ledger</strong>}
              </div>
              <div className="row">
                {perms.includes("sim:control") && ["ACTIVE", "PAUSE_REQUESTED", "ERROR"].includes(c.status) && (
                  <button type="button" disabled={busy} onClick={() => run(() => api.simCampaignDays(token, c.id, 1))}>
                    Simular 1 dia
                  </button>
                )}
                {perms.includes("proposals:create") && ["ACTIVE", "ERROR"].includes(c.status) && (
                  <button type="button" disabled={busy}
                    onClick={() => run(() => api.pauseCampaign(token, c.id, "pausa solicitada pelo escritório"), true)}>
                    Propor pausa
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function OrionPanel({ token, refreshSignal }: { token: string; refreshSignal: number }) {
  const { items, error } = useCampaigns(token, refreshSignal);
  const [table, setTable] = useState(false);
  if (!items) return error ? <p className="err small">{error}</p> : null;
  const withSpend = items.filter((c) => c.spend_minor > 0);
  const imp = items.reduce((s, c) => s + c.impressions, 0);
  const clk = items.reduce((s, c) => s + c.clicks, 0);
  const conv = items.reduce((s, c) => s + c.conversions, 0);
  const spend = items.reduce((s, c) => s + c.spend_minor, 0);
  const rev = items.reduce((s, c) => s + c.revenue_minor, 0);
  const maxRoas = Math.max(0, ...withSpend.map((c) => c.roas_x100 ?? 0));
  return (
    <div className="dept sales">
      <h3>Atribuição de mídia {items.some((c) => c.simulated) && <span className="sim">Simulado</span>}</h3>
      <div className="kpis">
        <div><span className="muted small">Impressões</span><strong>{imp.toLocaleString("pt-BR")}</strong></div>
        <div><span className="muted small">Cliques · CTR</span>
          <strong>{clk.toLocaleString("pt-BR")} · {imp ? formatBps(Math.floor((clk * 10_000) / imp)) : "—"}</strong></div>
        <div><span className="muted small">Conversões · CPA</span>
          <strong>{conv} · {conv ? formatMinor(Math.floor(spend / conv)) : "—"}</strong></div>
        <div><span className="muted small">ROAS geral</span><strong>{spend ? roas(Math.floor((rev * 100) / spend)) : "—"}</strong></div>
      </div>
      <div className="row between">
        <h4>ROAS por campanha <span className="muted small">(receita atribuída ÷ gasto)</span></h4>
        {withSpend.length > 0 && (
          <button type="button" className="link" onClick={() => setTable((v) => !v)}>{table ? "Ver gráfico" : "Ver tabela"}</button>
        )}
      </div>
      {withSpend.length === 0 ? <p className="muted small">Sem gasto importado ainda.</p> : table ? (
        <table className="data"><thead><tr><th>Campanha</th><th>Gasto</th><th>Receita</th><th>ROAS</th></tr></thead>
          <tbody>{withSpend.map((c) => (
            <tr key={c.id}><td>{c.name}</td><td>{formatMinor(c.spend_minor)}</td><td>{formatMinor(c.revenue_minor)}</td>
              <td>{roas(c.roas_x100)}</td></tr>))}</tbody></table>
      ) : (
        <ul className="hbars" aria-label="ROAS por campanha">
          {withSpend.map((c) => (
            <li key={c.id} title={`${c.name}: ROAS ${roas(c.roas_x100)} · gasto ${formatMinor(c.spend_minor)} · receita ${formatMinor(c.revenue_minor)}`}>
              <span className="hb-label">{c.name}</span>
              <span className="hb-track"><span className="hb-bar" style={{ width: `${barPct(c.roas_x100 ?? 0, maxRoas)}%` }} /></span>
              <span className="hb-value">{roas(c.roas_x100)}</span>
            </li>))}
        </ul>
      )}
      <p className="muted small">Atribuição de último clique informada pela plataforma; não é receita conciliada do canal.</p>
    </div>
  );
}

const OUTCOME_CLASS: Record<string, string> = { CONFIRMADA: "ok", PENDENTE: "warn", FALHOU: "err" };

export function StopReportPanel({ token, refreshSignal, onClose }: { token: string; refreshSignal: number; onClose(): void }) {
  const [r, setR] = useState<StopReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.stopReport(token).then(setR).catch((e) => setError(msg(e))); }, [token, refreshSignal]);
  return (
    <aside className="panel" aria-label="Relatório do kill switch">
      <header className="panel-head">
        <h2>Relatório do kill switch</h2>
        <button type="button" onClick={onClose} aria-label="Fechar relatório">✕</button>
      </header>
      {error && <p className="err" role="alert">{error}</p>}
      {r && (r.message ? <p className="muted">{r.message}</p> : (
        <>
          <dl className="kv">
            <dt>Estado</dt><dd>{r.active ? "ATIVO" : `liberado em ${new Date(r.released_at ?? "").toLocaleString("pt-BR")}`}</dd>
            <dt>Motivo</dt><dd>{r.reason}</dd>
            <dt>Acionado</dt><dd>{r.activated_at && new Date(r.activated_at).toLocaleString("pt-BR")}</dd>
            <dt>Delegações revogadas</dt><dd>{r.revoked_delegations?.join(", ") || "nenhuma"}</dd>
            <dt>Gasto após o stop</dt><dd>{formatMinor(r.spend_since_stop_minor ?? 0)}</dd>
          </dl>
          <h3>Pausas externas ({r.pause_counts?.CONFIRMADA ?? 0}/{r.pauses?.length ?? 0} confirmadas)</h3>
          {r.pauses?.length === 0 ? <p className="muted small">Nada publicado ou ativo para pausar.</p> : (
            <ul className="list">
              {r.pauses?.map((p) => (
                <li key={`${p.kind}-${p.subject_id}`} className="small">
                  <span className={OUTCOME_CLASS[p.outcome]}>{p.outcome}</span> · {p.kind === "LISTING" ? "Anúncio" : "Campanha"}{" "}
                  {p.channel} <code>{p.subject_id.slice(0, 8)}</code>
                  <span className="muted"> · proposta {p.proposal_status ?? "—"} · execução {p.job_status ?? "—"}</span>
                  {p.outcome === "PENDENTE" && p.proposal_status === "PENDING_APPROVAL" && (
                    <span className="warn"> — aguarda aprovação na sala do CEO</span>)}
                </li>
              ))}
            </ul>
          )}
          <p className="muted small">Jobs congelados na fila: {r.frozen_jobs?.length ?? 0}. {r.note}</p>
        </>
      ))}
    </aside>
  );
}
