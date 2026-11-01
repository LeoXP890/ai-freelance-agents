"use client";
// Rotinas do agente: tarefas que se repetem sozinhas (todo dia num horário de Brasília, ou a cada N horas). O servidor
// dispara no horário; a tarefa segue as mesmas regras de um pedido manual (orçamento de IA, aprovação de efeitos).
import { Pause, Play, Plus, Repeat, Trash2 } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, type Routine, type RoutineKind } from "@/lib/api";
import { since } from "@/lib/agentIdentity";
import { when } from "@/lib/routines";
import { useDesk } from "./context";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");
const OUTCOME: Record<string, string> = {
  DISPARADA: "executou no horário", MANUAL: "executada agora por você",
  PULADA_EM_ANDAMENTO: "pulada: a anterior ainda não terminou", PULADA_EMERGENCIA: "pulada: parada de emergência ativa",
};

export default function AgentRoutines({ agentId }: { agentId: string }) {
  const desk = useDesk();
  const canEdit = desk.can("tasks:create");
  const [items, setItems] = useState<Routine[] | null>(null);
  const [kinds, setKinds] = useState<RoutineKind[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ kind: "", schedule: "DAILY" as "DAILY" | "INTERVAL", time: "08:00", hours: 24, text: "" });
  const load = useCallback(() => {
    api.routines(desk.token, agentId).then((r) => {
      setItems(r.items);
      const mine = r.kinds.filter((k) => k.agent_id === agentId);
      setKinds(mine);
      setForm((f) => (f.kind || !mine[0] ? f : { ...f, kind: mine[0].kind }));
    }).catch((e) => setError(msg(e)));
  }, [desk.token, agentId]);
  useEffect(() => { load(); }, [load, desk.refresh]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setError(null);
    try { await fn(); desk.notify(ok); load(); } catch (e) { setError(msg(e)); }
  }
  async function create(e: FormEvent) {
    e.preventDefault();
    const daily = form.schedule === "DAILY";
    await act(() => api.createRoutine(desk.token, {
      agent_id: agentId, kind: form.kind, schedule: form.schedule,
      time_of_day: daily ? form.time : null, interval_hours: daily ? null : form.hours, text: form.text || null,
    }), "Rotina criada.");
    setForm((f) => ({ ...f, text: "" }));
  }
  const kind = kinds.find((k) => k.kind === form.kind);

  return (
    <div className="dk-card" aria-label="Rotinas do agente">
      <div className="card-title"><Repeat size={15} aria-hidden="true" /> Rotinas</div>
      <p className="faint" style={{ marginTop: 0 }}>Tarefas que este agente faz sozinho, no horário de Brasília. Cada execução usa o orçamento de IA
        e qualquer efeito externo continua passando pela sua aprovação.</p>
      {error && <p role="alert" className="dk-error">{error}</p>}
      {items === null ? <p className="muted">Carregando…</p> : items.length === 0
        ? <p className="muted">Nenhuma rotina ainda.</p>
        : <div className="rows">{items.map((r) => (
          <div key={r.id} className="row-item">
            <span className="grow">
              <span className="t" style={{ display: "block" }}>{r.title}{r.text ? ` · ${r.text}` : ""}</span>
              <span className="meta">{when(r)} · {r.enabled
                ? (r.next_run_at ? `próxima ${new Date(r.next_run_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}` : "")
                : "pausada"}{r.last_run_at ? ` · última ${since(r.last_run_at)}${r.last_outcome ? ` (${OUTCOME[r.last_outcome] ?? r.last_outcome})` : ""}` : ""}</span>
            </span>
            {canEdit && <>
              <button type="button" className="icon-btn" title="Executar agora" aria-label={`Executar agora: ${r.title}`} disabled={!desk.online}
                onClick={() => act(() => api.runRoutine(desk.token, r.id), "Tarefa criada agora.")}><Play size={16} /></button>
              <button type="button" className="icon-btn" title={r.enabled ? "Pausar" : "Retomar"} aria-label={`${r.enabled ? "Pausar" : "Retomar"}: ${r.title}`} disabled={!desk.online}
                onClick={() => act(() => api.updateRoutine(desk.token, r.id, { enabled: !r.enabled }), r.enabled ? "Rotina pausada." : "Rotina retomada.")}>
                {r.enabled ? <Pause size={16} /> : <Repeat size={16} />}</button>
              <button type="button" className="icon-btn" title="Excluir" aria-label={`Excluir: ${r.title}`} disabled={!desk.online}
                onClick={() => act(() => api.deleteRoutine(desk.token, r.id), "Rotina excluída.")}><Trash2 size={16} /></button>
            </>}
          </div>))}</div>}
      {canEdit && (kinds.length === 0
        ? <p className="faint" style={{ marginBottom: 0 }}>Este agente ainda não tem trabalho que possa virar rotina.</p>
        : (
          <form onSubmit={create} style={{ display: "grid", gap: 8, marginTop: 12 }} aria-label="Nova rotina">
            <strong>Nova rotina</strong>
            <select className="field" aria-label="O que fazer" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {kinds.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
            </select>
            {kind?.field && (
              <input className="field" required minLength={3} maxLength={500} value={form.text} aria-label={kind.field_label ?? "Detalhe"}
                placeholder={kind.field_label ?? ""} onChange={(e) => setForm({ ...form, text: e.target.value })} />)}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <select className="field" aria-label="Frequência" value={form.schedule}
                onChange={(e) => setForm({ ...form, schedule: e.target.value as "DAILY" | "INTERVAL" })}>
                <option value="DAILY">Todo dia às</option>
                <option value="INTERVAL">A cada</option>
              </select>
              {form.schedule === "DAILY"
                ? <input className="field" type="time" required aria-label="Horário" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} />
                : <><input className="field" type="number" min={2} max={168} required aria-label="Horas" value={form.hours} style={{ width: 90 }}
                  onChange={(e) => setForm({ ...form, hours: Number(e.target.value) })} /><span className="faint">horas</span></>}
            </div>
            <button type="submit" className="btn primary" disabled={!desk.online}><Plus />Criar rotina</button>
          </form>
        ))}
    </div>
  );
}
