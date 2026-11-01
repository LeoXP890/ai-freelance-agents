"use client";
// Componentes base do design system (UXD-005). Estado = ícone + texto (cor nunca sozinha).
import { AlertTriangle, Check, Circle, Hand, Lightbulb, Loader, OctagonX, Wrench, X } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useId, useRef, useState } from "react";
import { STATE_UI } from "@/lib/agentIdentity";
import { TASK_STATE } from "@/lib/workflow";

const ICONS = { circle: Circle, lightbulb: Lightbulb, loader: Loader, wrench: Wrench, hand: Hand, octagon: OctagonX,
  check: Check, alert: AlertTriangle } as const;

/** Mantém o foco do teclado dentro do diálogo aberto (Tab/Shift+Tab circulam; auditoria A12). */
export function trapFocus(e: { key: string; shiftKey: boolean; preventDefault(): void }, root: HTMLElement | null): void {
  if (e.key !== "Tab" || !root) return;
  const items = [...root.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter((el) => el.offsetParent !== null || el === document.activeElement);
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0]!, last = items[items.length - 1]!;
  const active = document.activeElement as HTMLElement | null;
  if (e.shiftKey && (active === first || !root.contains(active))) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && (active === last || !root.contains(active))) { e.preventDefault(); first.focus(); }
}

export function StateChip({ state }: { state: string }) {
  const s = STATE_UI[state] ?? STATE_UI.IDLE!;
  const Icon = ICONS[s.icon as keyof typeof ICONS] ?? Circle;
  return <span className={`dk-chip ${s.tone}`}><Icon aria-hidden="true" />{s.label}</span>;
}

const TASK_ICON: Record<string, keyof typeof ICONS> = {
  RUNNING: "loader", WAITING_EXTERNAL: "wrench", WAITING_APPROVAL: "hand", RESUMED: "loader", COMPLETED: "check",
  FAILED: "alert", CANCELLED: "octagon",
};

/** Estado de tarefa/missão do backend (agent_tasks) com ícone + texto. */
export function TaskStateChip({ state, label }: { state: string; label?: string }) {
  const s = TASK_STATE[state] ?? { label: state, tone: "muted" as const };
  const Icon = ICONS[TASK_ICON[state] ?? "circle"];
  return <span className={`dk-chip ${s.tone}`}><Icon aria-hidden="true" />{label ?? s.label}</span>;
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return <div className="empty">{icon}<strong>{title}</strong>{children && <span className="faint">{children}</span>}</div>;
}

export function Kpi({ label, value, sub, icon }: { label: string; value: ReactNode; sub: string; icon?: ReactNode }) {
  return (
    <div className="dk-card dk-kpi">
      <div className="lbl">{icon}{label}</div>
      <div className="val num">{value}</div>
      <div className="sub">{sub}</div>
    </div>
  );
}

/** Confirmação com justificativa obrigatória — usada para ações sensíveis; a decisão final é sempre do backend. */
export function ConfirmDialog({ title, body, confirmLabel, danger, needReason, onConfirm, onCancel }: {
  title: string; body: ReactNode; confirmLabel: string; danger?: boolean; needReason?: boolean;
  onConfirm(reason: string): Promise<void> | void; onCancel(): void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const first = useRef<HTMLTextAreaElement | HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  async function go() {
    setBusy(true); setError(null);
    try { await onConfirm(reason.trim()); } catch (e) { setError(e instanceof Error ? e.message : String(e)); setBusy(false); }
  }
  return (
    <div className="palette-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="palette" role="alertdialog" aria-modal="true" aria-labelledby={`${id}-t`} style={{ padding: 20 }}
        ref={box} onKeyDown={(e) => trapFocus(e, box.current)}>
        <h2 id={`${id}-t`} style={{ fontSize: 18, marginBottom: 8 }}>{title}</h2>
        <div className="muted" style={{ marginBottom: 12 }}>{body}</div>
        {needReason && (
          <label style={{ display: "grid", gap: 6, marginBottom: 12 }}>
            <span className="faint">Motivo (fica registrado na auditoria)</span>
            <textarea ref={first as RefObject<HTMLTextAreaElement>} className="field" style={{ height: 72, padding: 10 }}
              value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        )}
        {error && <p role="alert" style={{ color: "var(--critical)", margin: "0 0 12px" }}>{error}</p>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn ghost" onClick={onCancel}><X />Cancelar</button>
          <button type="button" ref={needReason ? undefined : (first as RefObject<HTMLButtonElement>)}
            className={`btn ${danger ? "danger" : "primary"}`} disabled={busy || (needReason && reason.trim().length < 3)} onClick={go}>
            {busy ? "Enviando…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
