"use client";
// UX-04 Missões & Workflow (UXD-010): lista com responsáveis, objetivo, estado, custo, duração e último evento;
// detalhe com etapas e linha do tempo REAL (eventos de estado + checkpoints do backend). Cancelar e reexecutar
// passam pelo backend com confirmação; nenhuma etapa é animada ou marcada como feita localmente.
import { Ban, Clock, Flag, Inbox, Plus, RotateCcw, Search, Workflow, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type Mission, type MissionRow } from "@/lib/api";
import { since } from "@/lib/agentIdentity";
import { formatMicroUsd } from "@/lib/format";
import { CHECKPOINT_LABEL, describeEvent, formatDuration, MISSION_BLOCKER } from "@/lib/workflow";
import AgentPortrait from "../AgentPortrait";
import { useDesk } from "../context";
import { Skeleton } from "../table";
import { ConfirmDialog, Empty, TaskStateChip } from "../ui";

type Filter = "OPEN" | "DONE" | "ALL";
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export default function Missions() {
  const desk = useDesk();
  const [filter, setFilter] = useState<Filter>("ALL");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<MissionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const canRun = desk.can("ai:missions");

  // eventos de agentes (WS) mudam missões: recarrega a lista quando o feed avança
  const tick = desk.office.lastSeq;
  const load = useCallback(() => {
    api.missions(desk.token, filter === "ALL" ? undefined : filter)
      .then((r) => { setRows(r.items); setError(null); })
      .catch((e) => setError(msg(e)));
  }, [desk.token, filter]);
  useEffect(() => { load(); }, [load, tick, desk.refresh]);

  const shown = useMemo(() => (rows ?? []).filter((m) => !q || m.goal.toLowerCase().includes(q.toLowerCase())), [rows, q]);
  const open = rows?.filter((m) => !["COMPLETED", "FAILED", "CANCELLED"].includes(m.state)).length ?? 0;
  const blocked = rows?.filter((m) => m.blocker && m.blocker !== "WAITING_TOOL").length ?? 0;

  return (
    <div className="page split-page">
      <div className="page-head">
        <div>
          <h1>Missões & tarefas</h1>
          <p>Objetivos coordenados pelo Atlas e executados pelos agentes, com custo e histórico reais.</p>
        </div>
        <div className="actions">
          {canRun && <button type="button" className="btn primary" disabled={!desk.online} onClick={() => setCreating(true)}><Plus />Nova missão</button>}
        </div>
      </div>
      {creating && <NewMission onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setSelected(id); load(); }} />}

      <div className="filters">
        <div className="dk-seg" role="group" aria-label="Filtrar missões">
          {([["ALL", "Todas"], ["OPEN", "Em andamento"], ["DONE", "Encerradas"]] as [Filter, string][]).map(([k, l]) => (
            <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
        <label className="search-field"><Search size={16} aria-hidden="true" />
          <input className="field" type="search" placeholder="Buscar pelo objetivo" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar missões pelo objetivo" />
        </label>
        <span className="faint" style={{ marginLeft: "auto" }}>{rows ? `${rows.length} missões · ${open} em andamento · ${blocked} exigindo atenção` : ""}</span>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}

      <div className={`split ${selected ? "with-detail" : ""}`}>
        <section className="dk-card table-card" aria-label="Lista de missões">
          {rows === null ? <Skeleton /> : shown.length === 0 ? (
            <Empty icon={<Workflow />} title={rows.length ? "Nenhuma missão com esse filtro" : "Nenhuma missão ainda"}>
              {canRun ? "Crie uma missão com um objetivo (ex.: avaliar um nicho) e o Atlas distribui as etapas aos agentes." : "Seu papel acompanha missões, mas não cria."}
            </Empty>
          ) : (
            <table className="dk-table">
              <thead><tr><th>Missão</th><th>Agentes</th><th>Estado</th><th>Etapas</th><th className="r">Custo IA</th><th className="r opt">Duração</th><th className="opt">Último evento</th></tr></thead>
              <tbody>
                {shown.map((m) => (
                  <tr key={m.id} aria-selected={selected === m.id} onClick={() => setSelected(m.id)}>
                    <td>
                      <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); setSelected(m.id); }}
                        aria-label={`Abrir missão: ${m.goal}`}>{m.goal}</button>
                      <div className="faint">#{m.id.slice(0, 8)}{m.rerun_of ? " · reexecução" : ""}{m.simulated ? " · simulada" : ""}</div>
                    </td>
                    <td><Faces ids={m.agents} /></td>
                    <td><MissionState m={m} /></td>
                    <td><Progress done={m.steps_done} failed={m.steps_failed} total={m.steps_total} /></td>
                    <td className="r num">{formatMicroUsd(m.cost_micro_usd)}</td>
                    <td className="r num opt">{formatDuration(m.duration_s)}</td>
                    <td className="faint opt">{since(m.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        {selected && <MissionDetail id={selected} tick={tick} onClose={() => setSelected(null)} onChanged={load}
          onOpen={(id) => setSelected(id)} />}
      </div>
    </div>
  );
}

export function useAgentName() {
  const desk = useDesk();
  return (id: string) => desk.office.agents[id]?.name ?? id.charAt(0).toUpperCase() + id.slice(1);
}

function Faces({ ids }: { ids: string[] }) {
  const nameOf = useAgentName();
  return (
    <span className="faces" aria-label={ids.map(nameOf).join(", ")}>
      {ids.slice(0, 5).map((i) => <AgentPortrait key={i} agentId={i} size={26} />)}
      {ids.length > 5 && <span className="faint">+{ids.length - 5}</span>}
    </span>
  );
}

function MissionState({ m }: { m: Pick<MissionRow, "state" | "blocker"> }) {
  if (m.blocker && m.state !== "CANCELLED") {
    const b = MISSION_BLOCKER[m.blocker];
    return <TaskStateChip state={m.blocker === "WAITING_APPROVAL" ? "WAITING_APPROVAL" : m.state} label={b.label} />;
  }
  return <TaskStateChip state={m.state} />;
}

function Progress({ done, failed, total }: { done: number; failed: number; total: number }) {
  if (!total) return <span className="faint">planejando</span>;
  const pct = Math.round((done / total) * 100);
  return (
    <span className="progress" title={`${done} de ${total} etapas concluídas${failed ? `, ${failed} com falha` : ""}`}>
      <span className="bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
      <span className="num">{done}/{total}</span>{failed > 0 && <span className="faint" style={{ color: "var(--critical)" }}>{failed} falha</span>}
    </span>
  );
}

function NewMission({ onClose, onCreated }: { onClose(): void; onCreated(id: string): void }) {
  const desk = useDesk();
  const [goal, setGoal] = useState("");
  const [steps, setSteps] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api.createMission(desk.token, goal.trim(), steps);
      desk.notify("Missão criada no servidor — o Atlas está planejando as etapas.");
      onCreated(r.mission_id);
    } catch (err) { setError(msg(err)); setBusy(false); }
  }
  return (
    <form className="dk-card new-mission" onSubmit={submit} aria-label="Nova missão">
      <div className="card-title"><Flag size={16} />Nova missão{desk.office.simulated && <span className="dk-chip demo">SIMULAÇÃO</span>}
        <button type="button" className="icon-btn" style={{ marginLeft: "auto" }} onClick={onClose} aria-label="Fechar nova missão"><X size={16} /></button></div>
      <label className="stack-field"><span className="faint">Objetivo (o que você quer descobrir ou preparar)</span>
        <textarea className="field" rows={2} minLength={5} maxLength={500} required autoFocus value={goal}
          placeholder="Ex.: avaliar o nicho de organizadores de cozinha para o Mercado Livre" onChange={(e) => setGoal(e.target.value)} /></label>
      <div className="row-actions">
        <label className="inline-field"><span className="faint">Máximo de etapas</span>
          <select className="field" value={steps} onChange={(e) => setSteps(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
          </select></label>
        <span className="faint">Missões só pesquisam e preparam. Gastos e publicações viram propostas para a sua aprovação.</span>
        <button type="submit" className="btn primary" disabled={busy || goal.trim().length < 5 || !desk.online}>{busy ? "Enviando…" : "Iniciar missão"}</button>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
    </form>
  );
}

function MissionDetail({ id, tick, onClose, onChanged, onOpen }: {
  id: string; tick: number; onClose(): void; onChanged(): void; onOpen(id: string): void;
}) {
  const desk = useDesk();
  const [m, setM] = useState<Mission | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [busy, setBusy] = useState(false);
  const nameOf = useAgentName();
  useEffect(() => {
    let alive = true;
    api.mission(desk.token, id).then((r) => { if (alive) { setM(r); setError(null); } }).catch((e) => alive && setError(msg(e)));
    return () => { alive = false; };
  }, [desk.token, id, tick, desk.refresh]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !cancelling) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, cancelling]);

  const timeline = useMemo(() => {
    if (!m) return [];
    const names: Record<string, string> = { [m.id]: "Atlas · plano da missão" };
    m.children.forEach((c, i) => { names[c.id] = `${nameOf(c.agent_id)} · etapa ${i + 1}`; });
    const ev = (m.timeline ?? []).map((e) => ({ at: e.at, key: `e${e.seq}`, who: names[e.task_id] ?? e.task_id.slice(0, 8),
      agent: e.agent_id, text: describeEvent(e), kind: e.task_state ?? "tool" }));
    const cp = (m.checkpoints ?? []).filter((c) => c.task_id === m.id).map((c, i) => ({ at: c.at, key: `c${i}`,
      who: "Atlas · checkpoint", agent: "atlas", text: CHECKPOINT_LABEL[c.node] ?? c.node, kind: "checkpoint" }));
    return [...ev, ...cp].sort((a, b) => a.at.localeCompare(b.at));
  }, [m]); // eslint-disable-line react-hooks/exhaustive-deps

  const terminal = m && ["COMPLETED", "FAILED", "CANCELLED"].includes(m.state);
  async function rerun() {
    if (!m) return;
    setBusy(true);
    try {
      const r = await api.rerunMission(desk.token, m.id);
      desk.notify("Nova missão criada com o mesmo objetivo; a original continua no histórico.");
      onChanged(); onOpen(r.mission_id);
    } catch (e) { setError(msg(e)); } finally { setBusy(false); }
  }

  return (
    <aside className="dk-card detail-pane" aria-label="Detalhe da missão">
      <div className="detail-head">
        <div style={{ minWidth: 0 }}>
          <div className="faint">Missão #{id.slice(0, 8)}{m?.simulated ? " · simulada (DRY_RUN)" : ""}</div>
          <h2 className="detail-title">{m?.goal ?? "Carregando…"}</h2>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fechar detalhe da missão"><X size={18} /></button>
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      {m && (
        <div className="detail-body">
          <div className="chip-row">
            <MissionState m={m} />
            <span className="dk-chip"><Clock aria-hidden="true" />{formatDuration(m.duration_s)}</span>
            <span className="dk-chip num">IA {formatMicroUsd(m.cost_micro_usd)}</span>
            <span className="faint">atualizada {since(m.updated_at)}</span>
          </div>
          {m.blocker && m.state !== "CANCELLED" && (
            <div className={`callout ${MISSION_BLOCKER[m.blocker].tone}`} role="note">
              <strong>{MISSION_BLOCKER[m.blocker].label}.</strong> {MISSION_BLOCKER[m.blocker].hint}
              {m.error && m.state === "FAILED" && <div className="faint" style={{ marginTop: 4 }}>Detalhe: {m.error}</div>}
              {m.blocker === "WAITING_APPROVAL" && desk.can("approvals:read") && (
                <div style={{ marginTop: 8 }}><button type="button" className="btn" onClick={() => desk.go("approvals")}><Inbox />Abrir aprovações</button></div>
              )}
            </div>
          )}
          {m.state === "CANCELLED" && m.error && <div className="callout muted" role="note">Cancelada: {m.error.replace(/^CANCELLED:\s*/, "")}</div>}
          {m.rerun_of && <p className="faint" style={{ margin: 0 }}>Reexecução da missão <button type="button" className="dk-link" onClick={() => onOpen(m.rerun_of!)}>#{m.rerun_of.slice(0, 8)}</button></p>}

          {desk.can("ai:missions") && (
            <div className="row-actions">
              {!terminal && <button type="button" className="btn danger" disabled={!desk.online} onClick={() => setCancelling(true)}><Ban />Cancelar missão</button>}
              {terminal && <button type="button" className="btn" disabled={busy || !desk.online} onClick={rerun}><RotateCcw />Reexecutar simulação</button>}
            </div>
          )}

          <section aria-label="Etapas">
            <h3 className="sec-title">Etapas ({m.steps_done}/{m.steps_total || "—"})</h3>
            {m.children.length === 0 ? <p className="muted" style={{ margin: 0 }}>{terminal ? "Nenhuma etapa foi criada." : "O Atlas está montando o plano…"}</p> : (
              <ol className="steps">
                {m.children.map((c, i) => (
                  <li key={c.id} className="step">
                    <span className="step-n" aria-hidden="true">{i + 1}</span>
                    <div style={{ minWidth: 0 }}>
                      <div className="step-head">
                        <AgentPortrait agentId={c.agent_id} size={24} />
                        <strong>{nameOf(c.agent_id)}</strong>
                        <span className="muted ellipsis">{desk.taskTitle(c.kind)}</span>
                        <span style={{ marginLeft: "auto" }}><TaskStateChip state={c.state} /></span>
                      </div>
                      {c.why && <div className="faint">Por quê: {c.why}</div>}
                      {c.summary && <div className="step-sum">{c.summary}</div>}
                      {c.error && <div className="step-err">{c.error}</div>}
                      <div className="faint">{c.cost_micro_usd !== undefined ? `IA ${formatMicroUsd(c.cost_micro_usd)} · ` : ""}{c.attempts ? `${c.attempts} tentativa(s) · ` : ""}{since(c.updated_at)}</div>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {m.result?.summary && (
            <section aria-label="Resultado">
              <h3 className="sec-title">Resultado</h3>
              <p style={{ margin: 0 }}>{m.result.summary}</p>
            </section>
          )}

          <section aria-label="Linha do tempo">
            <h3 className="sec-title">Linha do tempo</h3>
            <ol className="timeline">
              {timeline.map((t) => (
                <li key={t.key} data-kind={t.kind}>
                  <time dateTime={t.at} className="faint num">{new Date(t.at).toLocaleTimeString("pt-BR")}</time>
                  <span className="dot" aria-hidden="true" />
                  <span><span className="muted">{t.who}:</span> {t.text}</span>
                </li>
              ))}
            </ol>
            <p className="faint" style={{ margin: "6px 0 0" }}>Fonte: eventos de estado e checkpoints gravados pelo servidor.</p>
          </section>
        </div>
      )}
      {cancelling && m && (
        <ConfirmDialog title="Cancelar esta missão?" danger needReason confirmLabel="Cancelar missão"
          body={<>As etapas ainda abertas serão canceladas no servidor e nenhum resultado novo será gravado. O histórico permanece.</>}
          onCancel={() => setCancelling(false)}
          onConfirm={async (reason) => {
            const r = await api.cancelMission(desk.token, m.id, reason);
            setM(r); setCancelling(false); onChanged();
            desk.notify("Missão cancelada no servidor.", "warn");
          }} />
      )}
    </aside>
  );
}
