"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, type Me } from "@/lib/api";
import type { OfficeEventV1, PresenceSnapshotV1, PresenceV1 } from "@/lib/contract";
import { applyAppearance, applyEvent, applySnapshot, initialOffice, type OfficeState } from "@/lib/officeStore";
import { startOfficeSync, type Transport } from "@/lib/officeSync";
import { affectsApprovals, affectsCommerce } from "@/lib/format";
import AgentPanel from "./AgentPanel";
import ApprovalsPanel from "./ApprovalsPanel";
import PasswordDialog from "./PasswordDialog";
import UsersPanel from "./UsersPanel";
import { StopReportPanel } from "./AdsPanels";
import Login from "./Login";
import Office2D from "./Office2D";

const Office3D = dynamic(() => import("./Office3D"), {
  ssr: false,
  loading: () => <div className="loading">Carregando cena 3D…</div>,
});

type Action = { t: "snapshot"; s: PresenceSnapshotV1 } | { t: "event"; e: OfficeEventV1 } | { t: "reset" }
  | { t: "appearance"; agentId: string; color: string; appearance: PresenceV1["appearance"] };
function reducer(state: OfficeState, a: Action): OfficeState {
  if (a.t === "snapshot") return applySnapshot(state, a.s);
  if (a.t === "event") return applyEvent(state, a.e);
  if (a.t === "appearance") return applyAppearance(state, a.agentId, a.color, a.appearance);
  return initialOffice;
}

const TOKEN_KEY = "aic.token";
const VIEW_KEY = "aic.view";
const safeGet = (k: string, store: () => Storage) => { try { return store().getItem(k); } catch { return null; } };
const safeSet = (k: string, v: string | null, store: () => Storage) => {
  try { if (v === null) store().removeItem(k); else store().setItem(k, v); } catch { /* armazenamento indisponível */ }
};

function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

const TRANSPORT_LABEL: Record<Transport, string> = {
  connecting: "Conectando…", websocket: "Tempo real (WebSocket)", polling: "Tempo real (HTTP)", offline: "Offline",
};

export default function OfficeApp() {
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [office, dispatch] = useReducer(reducer, initialOffice);
  const lastSeq = useRef(0);
  lastSeq.current = office.lastSeq;
  const [transport, setTransport] = useState<Transport>("connecting");
  const [detail, setDetail] = useState<string | undefined>();
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"3d" | "2d">("3d");
  const [webgl, setWebgl] = useState(true);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [quality, setQuality] = useState<"low" | "high">("high");
  const [side, setSide] = useState<"feed" | "approvals" | "password" | "users" | "stop">("feed");
  const [pending, setPending] = useState(0);
  const [approvalsSignal, setApprovalsSignal] = useState(0);
  const [commerceSignal, setCommerceSignal] = useState(0);

  useEffect(() => {
    setToken(safeGet(TOKEN_KEY, () => sessionStorage));
    const gl = hasWebGL();
    setWebgl(gl);
    const saved = safeGet(VIEW_KEY, () => localStorage);
    setView(!gl ? "2d" : saved === "2d" ? "2d" : "3d");
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const onChange = () => setReducedMotion(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const logout = useCallback(() => {
    safeSet(TOKEN_KEY, null, () => sessionStorage);
    setToken(null);
    setMe(null);
    dispatch({ t: "reset" });
  }, []);

  useEffect(() => {
    if (!token) return;
    api.me(token).then(setMe).catch(logout);
    return startOfficeSync(token, {
      onSnapshot: (s) => dispatch({ t: "snapshot", s }),
      onEvent: (e) => dispatch({ t: "event", e }),
      onTransport: (t, d) => { setTransport(t); setDetail(d); },
      getLastSeq: () => lastSeq.current,
    });
  }, [token, logout]);

  const latest = office.feed[0];
  useEffect(() => {
    if (!token || !me?.permissions.includes("approvals:read")) return;
    api.approvals(token, "PENDING_APPROVAL").then((r) => setPending(r.items.length)).catch(() => undefined);
  }, [token, me, approvalsSignal]);
  useEffect(() => {
    if (latest && affectsApprovals(latest.type)) setApprovalsSignal((n) => n + 1);
    if (latest && affectsCommerce(latest.type)) setCommerceSignal((n) => n + 1);
  }, [latest]);

  const replaceToken = useCallback((t: string) => {
    safeSet(TOKEN_KEY, t, () => sessionStorage);
    setToken(t);
  }, []);

  if (!token) {
    return <Login onToken={(t) => { safeSet(TOKEN_KEY, t, () => sessionStorage); setToken(t); }} />;
  }

  const chooseView = (v: "3d" | "2d") => { setView(v); safeSet(VIEW_KEY, v, () => localStorage); };
  const canCreate = !!me?.permissions.includes("tasks:create");
  const canPropose = !!me?.permissions.includes("proposals:create");
  const canReadApprovals = !!me?.permissions.includes("approvals:read");
  const canDecide = !!me?.permissions.includes("approvals:decide");
  const openApprovals = () => { setSelected(null); setSide("approvals"); };
  const selectAgent = (id: string | null) => { setSelected(id); if (id) setSide("feed"); };

  return (
    <div className="app">
      {office.simulated && (
        <div className="banner sim-banner" role="status">
          SIMULADO · DRY_RUN — dados e resultados são fictícios; nenhuma integração real foi homologada.
        </div>
      )}
      {office.emergencyStop && (
        <div className="banner stop-banner" role="alert">EMERGENCY STOP ATIVO — novas despesas e publicações travadas.
          {(me?.permissions.includes("emergency:stop") || me?.permissions.includes("audit:read")) && (
            <button type="button" className="link" onClick={() => { setSelected(null); setSide("stop"); }}>Ver relatório</button>
          )}
        </div>
      )}
      <header className="topbar">
        <div>
          <div className="muted small">AI COMMERCE</div>
          <h1>Escritório</h1>
        </div>
        <div className="controls">
          <span className={`pill t-${transport}`} title={detail}>{TRANSPORT_LABEL[transport]}</span>
          <span className="pill">seq {office.lastSeq}</span>
          <div className="seg" role="group" aria-label="Modo de visualização">
            <button type="button" aria-pressed={view === "3d"} disabled={!webgl} onClick={() => chooseView("3d")}
              title={webgl ? "" : "WebGL indisponível"}>3D</button>
            <button type="button" aria-pressed={view === "2d"} onClick={() => chooseView("2d")}>2D</button>
          </div>
          {view === "3d" && (
            <select aria-label="Qualidade gráfica" value={quality}
              onChange={(e) => setQuality(e.target.value as "low" | "high")}>
              <option value="high">Qualidade alta</option>
              <option value="low">Qualidade baixa</option>
            </select>
          )}
          {canReadApprovals && (
            <button type="button" className={pending > 0 ? "primary" : ""} onClick={openApprovals}
              aria-label={`Aprovações: ${pending} pendentes`}>Aprovações ({pending})</button>
          )}
          <span className="muted small">{me?.email}</span>
          {me?.permissions.includes("users:manage") && (
            <button type="button" onClick={() => { setSelected(null); setSide("users"); }}>Usuários</button>
          )}
          <button type="button" onClick={() => { setSelected(null); setSide("password"); }}>Trocar senha</button>
          <button type="button" onClick={logout}>Sair</button>
        </div>
      </header>
      <div className="stage">
        <div className="scene">
          {!office.ready ? <div className="loading">Baixando snapshot do backend…</div>
            : view === "3d" ? <Office3D office={office} selectedId={selected} onSelect={selectAgent}
                reducedMotion={reducedMotion} quality={quality} pendingApprovals={canReadApprovals ? pending : null}
                onOpenApprovals={openApprovals} />
            : <Office2D office={office} selectedId={selected} onSelect={selectAgent}
                pendingApprovals={canReadApprovals ? pending : null} onOpenApprovals={openApprovals} />}
        </div>
        {side === "approvals" ? (
          <ApprovalsPanel token={token} canDecide={canDecide} refreshSignal={approvalsSignal}
            onClose={() => setSide("feed")} />
        ) : side === "stop" ? (
          <StopReportPanel token={token} refreshSignal={commerceSignal} onClose={() => setSide("feed")} />
        ) : side === "users" ? (
          <UsersPanel token={token} myEmail={me?.email ?? ""} onClose={() => setSide("feed")} />
        ) : side === "password" ? (
          <PasswordDialog token={token} onChanged={replaceToken} onClose={() => setSide("feed")} />
        ) : selected ? (
          <AgentPanel key={selected} office={office} agentId={selected} token={token} canCreate={canCreate}
            canPropose={canPropose} onProposed={openApprovals} onClose={() => setSelected(null)}
            permissions={me?.permissions ?? []} commerceSignal={commerceSignal}
            onAppearance={(agentId, color, appearance) => dispatch({ t: "appearance", agentId, color, appearance })} />
        ) : (
          <aside className="panel" aria-label="Feed de eventos">
            <h2>Feed do backend</h2>
            <p className="muted small">Clique em um agente para abrir o painel. Estados vêm de agent_presence +
              office_events (seq ordenado).</p>
            <ol className="list" aria-live="polite">
              {office.feed.slice(0, 25).map((e) => (
                <li key={e.seq}><span className="muted small">#{e.seq}</span> {e.agent_id ?? "sistema"} · {e.type}</li>
              ))}
            </ol>
          </aside>
        )}
      </div>
    </div>
  );
}
