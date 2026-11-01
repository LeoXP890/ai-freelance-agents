"use client";
// AI Commerce Desktop (SDD v3): AppShell com navegação por objetivo, status de conexão, modo SIMULAÇÃO/PRODUÇÃO,
// atalhos (Ctrl/Cmd+1..5, Ctrl/Cmd+K) e workspace de agente. Mesmo backend, mesmos contratos e mesma
// sincronização (snapshot + WS com replay) da interface anterior, que segue disponível em ?ui=legacy.
import {
  BarChart3, Bell, MessageSquare, BookOpen, Bot, Boxes, Building2, ChevronDown, ChevronsLeft, ChevronsRight, CircleDollarSign, Gauge,
  History, Inbox, KeyRound, LogOut, Megaphone, PackageSearch, Search, Settings, ShieldCheck, ShoppingCart, Store, Truck, WifiOff, Workflow,
} from "lucide-react";
import { type ComponentType, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api, apiUrl, type Me } from "@/lib/api";
import type { OfficeEventV1, PresenceSnapshotV1, PresenceV1 } from "@/lib/contract";
import { affectsApprovals, affectsCommerce } from "@/lib/format";
import { applyAppearance, applyEvent, applySnapshot, initialOffice, type OfficeState } from "@/lib/officeStore";
import { startOfficeSync, type Transport } from "@/lib/officeSync";
import Login from "@/components/Login";
import PasswordDialog from "@/components/PasswordDialog";
import AgentDrawer from "./AgentDrawer";
import AgentPortrait from "./AgentPortrait";
import { type Desk, DeskContext, type PageId, TASK_FALLBACK } from "./context";
import { forgetDevice, isDesktop, rememberDevice, RENEW_MS, signInWithDevice } from "./device";
import { applyCloseBehavior, notifyOS, onNativeNav, setTrayStatus, updateCheck } from "./native";
import { trapFocus } from "./ui";
import Agents from "./pages/Agents";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import Campaigns from "./pages/Campaigns";
import Channels from "./pages/Channels";
import Chat from "./pages/Chat";
import CommandCenter from "./pages/CommandCenter";
import Finance from "./pages/Finance";
import Gates from "./pages/Gates";
import Knowledge from "./pages/Knowledge";
import Missions from "./pages/Missions";
import Office from "./pages/Office";
import Orders from "./pages/Orders";
import Products from "./pages/Products";
import Reports from "./pages/Reports";
import SettingsPage from "./pages/Settings";
import Suppliers from "./pages/Suppliers";
import "./desk.css";

type Action = { t: "snapshot"; s: PresenceSnapshotV1 } | { t: "event"; e: OfficeEventV1 } | { t: "reset" }
  | { t: "appearance"; agentId: string; color: string; appearance: PresenceV1["appearance"] };
function reducer(state: OfficeState, a: Action): OfficeState {
  if (a.t === "snapshot") return applySnapshot(state, a.s);
  if (a.t === "event") return applyEvent(state, a.e);
  if (a.t === "appearance") return applyAppearance(state, a.agentId, a.color, a.appearance);
  return initialOffice;
}

const TOKEN_KEY = "aic.token";
const PAGE_KEY = "aic.desk.page";
const SIDEBAR_KEY = "aic.desk.sidebar";
export const THEME_KEY = "aic.theme"; // "system" (padrão) | "dark" | "light"
const get = (k: string, s: () => Storage) => { try { return s().getItem(k); } catch { return null; } };
const set = (k: string, v: string | null, s: () => Storage) => {
  try { if (v === null) s().removeItem(k); else s().setItem(k, v); } catch { /* */ }
};

interface NavItem { id: PageId; label: string; icon: ComponentType<{ size?: number }>; perm?: string; shortcut?: string }
const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Visão geral", items: [
    { id: "command", label: "Centro de comando", icon: Gauge, shortcut: "1" },
    { id: "office", label: "Escritório 3D", icon: Building2, shortcut: "2" },
    { id: "approvals", label: "Aprovações", icon: Inbox, perm: "approvals:read", shortcut: "4" },
  ] },
  { group: "Operação", items: [
    { id: "agents", label: "Agentes", icon: Bot, shortcut: "3" },
    { id: "chat", label: "Conversas", icon: MessageSquare, perm: "ai:use" },
    { id: "missions", label: "Missões & tarefas", icon: Workflow },
    { id: "products", label: "Produtos & pesquisa", icon: PackageSearch, perm: "catalog:read" },
    { id: "suppliers", label: "Fornecedores", icon: Boxes, perm: "catalog:read" },
    { id: "channels", label: "Canais de venda", icon: Store, perm: "catalog:read" },
    { id: "campaigns", label: "Campanhas", icon: Megaphone, perm: "finance:read" },
    { id: "orders", label: "Pedidos & logística", icon: Truck, perm: "orders:read" },
  ] },
  { group: "Inteligência", items: [
    { id: "finance", label: "Financeiro", icon: CircleDollarSign, perm: "finance:read", shortcut: "5" },
    { id: "reports", label: "Relatórios", icon: BarChart3 },
    { id: "knowledge", label: "Base de conhecimento", icon: BookOpen },
  ] },
  { group: "Sistema", items: [
    { id: "gates", label: "Homologações", icon: ShieldCheck },
    { id: "audit", label: "Histórico & auditoria", icon: History },
    { id: "settings", label: "Configurações", icon: Settings },
  ] },
];
const ALL_ITEMS = NAV.flatMap((g) => g.items);
// Todas as telas são nativas do app novo (Fase 5 concluída); o escritório recebe a seleção de agente.
const PAGES: Record<Exclude<PageId, "office">, ComponentType> = {
  command: CommandCenter, approvals: Approvals, agents: Agents, chat: Chat, missions: Missions, products: Products, suppliers: Suppliers,
  channels: Channels, campaigns: Campaigns, orders: Orders, finance: Finance, reports: Reports, knowledge: Knowledge,
  gates: Gates, audit: Audit, settings: SettingsPage,
};
const TRANSPORT: Record<Transport, { label: string; tone: string }> = {
  connecting: { label: "Conectando…", tone: "warn" }, websocket: { label: "Conectado", tone: "ok" },
  polling: { label: "Conectado (HTTP)", tone: "ok" }, offline: { label: "Sem conexão", tone: "crit" },
};

export default function DesktopApp() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [office, dispatch] = useReducer(reducer, initialOffice);
  const lastSeq = useRef(0);
  lastSeq.current = office.lastSeq;
  const [transport, setTransport] = useState<Transport>("connecting");
  const [page, setPage] = useState<PageId>("command");
  const [agent, setAgent] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [palette, setPalette] = useState(false);
  const [menu, setMenu] = useState(false);
  const [changingPwd, setChangingPwd] = useState(false);
  const [pending, setPending] = useState<number | null>(null);
  const [approvalsSignal, setApprovalsSignal] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [specs, setSpecs] = useState<Record<string, string>>({});
  const [syncedAt, setSyncedAt] = useState<number | null>(null);
  const [toasts, setToasts] = useState<{ id: number; text: string; tone: "ok" | "warn" | "crit" }[]>([]);
  const wasOffline = useRef(false);
  const [epoch, setEpoch] = useState(0);
  const [themePref, setThemePrefState] = useState<"system" | "dark" | "light">("system");
  const [systemDark, setSystemDark] = useState(true);
  useEffect(() => {
    const p = get(THEME_KEY, () => localStorage);
    if (p === "dark" || p === "light" || p === "system") setThemePrefState(p);
    const m = window.matchMedia("(prefers-color-scheme: dark)");
    setSystemDark(m.matches);
    const on = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  const theme: "dark" | "light" = themePref === "system" ? (systemDark ? "dark" : "light") : themePref;
  const setThemePref = useCallback((p: "system" | "dark" | "light") => { setThemePrefState(p); set(THEME_KEY, p, () => localStorage); }, []);
  const [chatTarget, setChatTarget] = useState<Desk["chatTarget"]>(null);
  const [approvalFocus, setApprovalFocus] = useState<Desk["approvalFocus"]>(null);
  const notify = useCallback((text: string, tone: "ok" | "warn" | "crit" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-2), { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);

  useEffect(() => {
    const p = get(PAGE_KEY, () => localStorage) as PageId | null;
    if (p && ALL_ITEMS.some((i) => i.id === p)) setPage(p);
    setCollapsed(get(SIDEBAR_KEY, () => localStorage) === "1");
    const saved = get(TOKEN_KEY, () => sessionStorage);
    if (saved) { setToken(saved); setReady(true); }
    else {
      // app desktop: computador lembrado entra direto, sem tela de login
      void signInWithDevice().then((t) => {
        if (t) { set(TOKEN_KEY, t, () => sessionStorage); setToken(t); }
        setReady(true);
      });
    }
    // autoteste só existe em builds de teste (NEXT_PUBLIC_AIC_SELFTEST=1); o build de produção o elimina (auditoria A20)
    if (process.env.NEXT_PUBLIC_AIC_SELFTEST === "1") void import("./selftest").then((m) => m.runSelfTest());
  }, []);
  // renova o token de acesso em segundo plano (desktop lembrado)
  useEffect(() => {
    if (!token || !isDesktop()) return;
    const id = setInterval(() => {
      void signInWithDevice().then((t) => { if (t) { set(TOKEN_KEY, t, () => sessionStorage); setToken(t); } });
    }, RENEW_MS);
    return () => clearInterval(id);
  }, [token]);
  const logout = useCallback(() => {
    void forgetDevice();
    set(TOKEN_KEY, null, () => sessionStorage);
    setToken(null); setMe(null); setAgent(null); dispatch({ t: "reset" });
  }, []);
  useEffect(() => {
    if (!token) return;
    api.me(token).then(setMe).catch(async () => {
      // sessão expirou/caiu: no desktop lembrado reentra sozinho; o cofre só é limpo se o backend revogar
      const t = await signInWithDevice();
      if (t) { set(TOKEN_KEY, t, () => sessionStorage); setToken(t); }
      else { set(TOKEN_KEY, null, () => sessionStorage); setToken(null); setMe(null); dispatch({ t: "reset" }); }
    });
    api.aiSpecs(token).then((r) => setSpecs(Object.fromEntries(r.items.map((s) => [s.kind, s.title])))).catch(() => undefined);
    return startOfficeSync(token, {
      onSnapshot: (s) => { dispatch({ t: "snapshot", s }); setSyncedAt(Date.now()); },
      onEvent: (e) => { dispatch({ t: "event", e }); setSyncedAt(Date.now()); },
      onTransport: (t) => { setTransport(t); if (t === "websocket" || t === "polling") setSyncedAt(Date.now()); },
      getLastSeq: () => lastSeq.current,
    });
  }, [token, logout, epoch]);
  // queda de rede: o navegador avisa antes do backoff da sincronização
  useEffect(() => {
    const off = () => setTransport("offline");
    const on = () => setEpoch((n) => n + 1); // rede voltou: reinicia a sincronização (novo snapshot + replay)
    window.addEventListener("offline", off);
    window.addEventListener("online", on);
    return () => { window.removeEventListener("offline", off); window.removeEventListener("online", on); };
  }, []);
  // reconexão: recarrega as telas a partir do servidor (reconciliação; nada local é reaplicado)
  useEffect(() => {
    if (transport === "offline") { wasOffline.current = true; return; }
    if ((transport === "websocket" || transport === "polling") && wasOffline.current) {
      wasOffline.current = false;
      setRefresh((n) => n + 1); setApprovalsSignal((n) => n + 1);
      notify("Conexão restabelecida — dados atualizados do servidor.");
    }
  }, [transport, notify]);
  const latest = office.feed[0];
  useEffect(() => {
    if (latest && affectsApprovals(latest.type)) setApprovalsSignal((n) => n + 1);
    if (latest && affectsCommerce(latest.type)) setRefresh((n) => n + 1);
  }, [latest]);
  useEffect(() => {
    if (!token || !me?.permissions.includes("approvals:read")) return;
    api.approvals(token, "PENDING_APPROVAL").then((r) => setPending(r.items.length)).catch(() => undefined);
  }, [token, me, approvalsSignal]);

  const go = useCallback((p: PageId) => { setPage(p); set(PAGE_KEY, p, () => localStorage); setPalette(false); }, []);
  // shell nativo: menu/bandeja navegam; bandeja mostra aprovações pendentes; notificação com a janela em 2º plano
  useEffect(() => { void applyCloseBehavior(); }, []);
  // atualização: uma consulta por dia, só avisa (instalar é decisão do usuário em Configurações)
  useEffect(() => {
    if (!isDesktop() || !me) return;
    const last = Number(get("aic.update.checkedAt", () => localStorage) ?? 0);
    if (Date.now() - last < 24 * 3600_000) return;
    set("aic.update.checkedAt", String(Date.now()), () => localStorage);
    void updateCheck(apiUrl()).then((u) => { if (u) notify(`Versão ${u.version} do app disponível — instale em Configurações.`); }).catch(() => undefined);
  }, [me, notify]);
  useEffect(() => onNativeNav((p) => { if (ALL_ITEMS.some((i) => i.id === p)) go(p as PageId); }), [go]);
  const lastPending = useRef<number | null>(null);
  useEffect(() => {
    if (pending === null) return;
    void setTrayStatus(pending);
    if (lastPending.current !== null && pending > lastPending.current) {
      const n = pending - lastPending.current;
      void notifyOS(n === 1 ? "Nova aprovação pendente" : `${n} novas aprovações pendentes`, "Uma proposta de agente aguarda sua decisão na Central de Aprovações.");
    }
    lastPending.current = pending;
  }, [pending]);
  const wasStopped = useRef(false);
  useEffect(() => {
    if (office.emergencyStop && !wasStopped.current) void notifyOS("Kill switch ativado", "Novas despesas e publicações foram travadas.");
    wasStopped.current = office.emergencyStop;
  }, [office.emergencyStop]);
  const can = useCallback((perm: string) => !!me?.permissions.includes(perm), [me]);

  // atalhos só com a janela em foco (sem captura global)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); return; }
      if (mod && /^[1-5]$/.test(e.key)) {
        const it = ALL_ITEMS.find((i) => i.shortcut === e.key);
        if (it) { e.preventDefault(); go(it.id); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  const desk: Desk | null = useMemo(() => token ? {
    token, me, office, transport, pending, refresh, approvalsSignal, syncedAt, notify,
    online: transport !== "offline",
    theme, themePref, setThemePref,
    taskTitle: (kind: string) => specs[kind] ?? TASK_FALLBACK[kind] ?? kind,
    can, go, openAgent: setAgent,
    refreshApprovals: () => setApprovalsSignal((n) => n + 1),
    chatTarget,
    approvalFocus,
    openApproval: (id: string) => { setApprovalFocus((c) => ({ id, n: (c?.n ?? 0) + 1 })); setAgent(null); go("approvals"); },
    openChat: (agentId: string, conversationId?: string) => {
      setChatTarget((c) => ({ agentId, conversationId, n: (c?.n ?? 0) + 1 })); setAgent(null); go("chat");
    },
    replaceToken: (t: string) => {
      set(TOKEN_KEY, t, () => sessionStorage); setToken(t);
      // troca de senha invalida o computador lembrado (segurança): relembra com a sessão nova para não pedir login
      void rememberDevice(t).catch(() => undefined);
    },
    logout,
  } : null, [token, me, office, transport, pending, refresh, approvalsSignal, syncedAt, notify, specs, can, go, logout, chatTarget, approvalFocus, theme, themePref, setThemePref]);

  if (!ready) return null;
  if (!token || !desk) {
    return (
      <div className="desk login-wrap" data-theme={theme}>
        <div style={{ width: "min(420px, 92vw)" }}>
          <div className="brand" style={{ justifyContent: "center", marginBottom: 8 }}>
            <img src="/app-icon.svg" alt="" /><span style={{ fontSize: 18 }}>AI Commerce</span>
          </div>
          {isDesktop() && <p className="muted" style={{ textAlign: "center", margin: "0 0 12px" }}>Só nesta primeira vez: depois o app abre direto neste computador.</p>}
          <ForgotPassword />
          <div className="legacy"><Login onToken={(t) => {
            set(TOKEN_KEY, t, () => sessionStorage); setToken(t);
            void rememberDevice(t).catch(() => undefined);
          }} /></div>
        </div>
      </div>
    );
  }

  const visible = (i: NavItem) => !i.perm || can(i.perm);
  const current = ALL_ITEMS.find((i) => i.id === page)!;
  const conn = TRANSPORT[transport];
  return (
    <DeskContext.Provider value={desk}>
      <div className="desk" data-last-seq={office.lastSeq} data-theme={theme}>
        <aside className={`side ${collapsed ? "collapsed" : ""}`} aria-label="Navegação principal">
          <div className="brand"><img src="/app-icon.svg" alt="" /><span>AI Commerce</span></div>
          <nav className="nav" aria-label="Principal">
            {NAV.map((g) => {
              const items = g.items.filter(visible);
              if (!items.length) return null;
              return (
                <div key={g.group} role="group" aria-label={g.group}>
                  <div className="nav-group-title">{g.group}</div>
                  {items.map((i) => (
                    <button key={i.id} type="button" className="nav-item" aria-current={page === i.id ? "page" : undefined}
                      onClick={() => go(i.id)} title={collapsed ? i.label : i.shortcut ? `Ctrl/⌘+${i.shortcut}` : undefined}>
                      <i.icon size={18} /><span className="nav-label">{i.label}</span>
                      {i.id === "approvals" && !!pending && <span className="nav-count" aria-label={`${pending} pendentes`}>{pending}</span>}
                    </button>
                  ))}
                </div>
              );
            })}
          </nav>
          <div className="side-foot">
            <span className={`dk-chip ${office.simulated ? "demo" : "ok"}`}>{office.simulated ? "SIMULAÇÃO" : "PRODUÇÃO"}</span>
            <span className={`dk-chip ${conn.tone}`} role="status"><span aria-hidden="true">●</span><span className="lbl">{conn.label}</span></span>
            <button type="button" className="nav-item collapse" onClick={() => { setCollapsed(!collapsed); set(SIDEBAR_KEY, collapsed ? "0" : "1", () => localStorage); }}
              aria-label={collapsed ? "Expandir menu" : "Recolher menu"} title={collapsed ? "Expandir menu" : "Recolher menu"}>
              {collapsed ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
            </button>
          </div>
        </aside>
        <div className="main">
          <header className="dk-topbar">
            <div className="crumbs"><span>{NAV.find((g) => g.items.includes(current))?.group}</span><span aria-hidden="true">/</span><strong>{current.label}</strong></div>
            <div className="grow" />
            <button type="button" className="search" onClick={() => setPalette(true)} aria-label="Pesquisar e navegar (Ctrl/⌘+K)">
              <Search size={16} /><span>Pesquisar agentes, páginas…</span><kbd>⌘K</kbd>
            </button>
            {can("approvals:read") && (
              <button type="button" className="icon-btn" onClick={() => go("approvals")} aria-label={`Aprovações: ${pending ?? 0} pendentes`}>
                <Bell size={18} />{!!pending && <span className="dk-dot" />}
              </button>
            )}
            <div style={{ position: "relative" }}>
              <button type="button" className="btn ghost" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}
                title={me?.roles.join(", ")}>{me?.email}<ChevronDown /></button>
              {menu && (
                <div role="menu" aria-label="Conta" className="dk-card" style={{ position: "absolute", right: 0, top: 42, zIndex: 40, padding: 6, minWidth: 260, boxShadow: "var(--shadow)" }}
                  onKeyDown={(e) => { if (e.key === "Escape") setMenu(false); }}>
                  <button type="button" role="menuitem" className="nav-item" autoFocus onClick={() => { setMenu(false); setChangingPwd(true); }}><KeyRound size={16} />Trocar senha</button>
                  <button type="button" role="menuitem" className="nav-item" onClick={() => { setMenu(false); go("settings"); }}><Settings size={16} />Configurações</button>
                  <button type="button" role="menuitem" className="nav-item" onClick={() => { setMenu(false); logout(); }}>
                    <LogOut size={16} />{isDesktop() ? "Sair e esquecer este computador" : "Sair"}</button>
                </div>
              )}
            </div>
          </header>
          {transport === "offline" ? (
            <div className="mode-strip stop" role="alert">
              <WifiOff size={16} aria-hidden="true" />Sem conexão com o servidor — exibindo dados de {syncedAt ? new Date(syncedAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "antes da queda"}.
              Ações que alteram dados ficam bloqueadas até reconectar; os agentes continuam trabalhando no servidor.
            </div>
          ) : office.emergencyStop ? (
            <div className="mode-strip stop" role="alert">Kill switch ATIVO — novas despesas e publicações travadas; pausas solicitadas aos canais.</div>
          ) : office.simulated ? (
            <div className="mode-strip" role="status">SIMULAÇÃO (DRY_RUN) — pedidos, gastos e integrações são fictícios; nada foi homologado para produção.</div>
          ) : <div />}
          <main style={{ position: "relative", minHeight: 0 }}>
            {page === "office" ? <Office selected={agent} onSelect={setAgent} /> : (() => { const P = PAGES[page]; return <P />; })()}
            {agent && <AgentDrawer agentId={agent} onClose={() => setAgent(null)}
              onShowInOffice={page === "office" ? undefined : () => go("office")} />}
          </main>
        </div>
        {changingPwd && (
          <div className="palette-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setChangingPwd(false); }}>
            <div className="palette legacy" role="dialog" aria-modal="true" aria-label="Trocar senha" style={{ padding: 18 }}
              onKeyDown={(e) => { if (e.key === "Escape") setChangingPwd(false); trapFocus(e, e.currentTarget); }}>
              <PasswordDialog token={token} onChanged={desk.replaceToken} onClose={() => setChangingPwd(false)} />
            </div>
          </div>
        )}
        <div className="toasts" role="status" aria-live="polite">
          {toasts.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
        </div>
        {palette && <Palette items={ALL_ITEMS.filter(visible)} onPage={go} onAgent={(id) => { setAgent(id); setPalette(false); }}
          onChangePassword={() => { setPalette(false); setChangingPwd(true); }}
          onClose={() => setPalette(false)} />}
      </div>
    </DeskContext.Provider>
  );
}

function Palette({ items, onPage, onAgent, onChangePassword, onClose }: {
  items: NavItem[]; onPage(p: PageId): void; onAgent(id: string): void; onChangePassword(): void; onClose(): void;
}) {
  const desk = useDeskSafe();
  const box = useRef<HTMLDivElement>(null);
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const agents = desk ? desk.office.order.map((id) => desk.office.agents[id]!).filter(Boolean) : [];
  const actions = [{ key: "x-pwd", label: "Trocar minha senha", hint: "Conta", run: onChangePassword, icon: <KeyRound size={16} /> }]
    .filter((x) => !q || `${x.label} senha password`.toLowerCase().includes(q.toLowerCase()));
  const results = [
    ...actions,
    ...items.filter((p) => p.label.toLowerCase().includes(q.toLowerCase())).map((p) => ({ key: `p-${p.id}`, label: p.label, hint: "Página", run: () => onPage(p.id), icon: <p.icon size={16} /> })),
    ...agents.filter((a) => `${a.name} ${a.role}`.toLowerCase().includes(q.toLowerCase())).map((a) => ({
      key: `a-${a.agent_id}`, label: `${a.name} — ${a.role}`, hint: "Agente", run: () => onAgent(a.agent_id), icon: <AgentPortrait agentId={a.agent_id} size={22} /> })),
  ];
  return (
    <div className="palette-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Pesquisar e navegar" ref={box} onKeyDown={(e) => trapFocus(e, box.current)}>
        <input autoFocus value={q} placeholder="Ir para página ou agente…" aria-label="Pesquisar"
          onChange={(e) => { setQ(e.target.value); setI(0); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") { e.preventDefault(); setI((v) => Math.min(v + 1, results.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setI((v) => Math.max(v - 1, 0)); }
            if (e.key === "Enter") results[i]?.run();
          }} />
        <ul role="listbox" aria-label="Resultados">
          {results.length === 0 && <li className="faint" style={{ padding: 12 }}>Nada encontrado.</li>}
          {results.map((r, k) => (
            <li key={r.key} role="option" aria-selected={k === i}>
              <button type="button" data-active={k === i} onClick={r.run} onMouseEnter={() => setI(k)}>{r.icon}{r.label}<span className="hint">{r.hint}</span></button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ForgotPassword() {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ textAlign: "center", marginBottom: 12 }}>
      <button type="button" className="btn ghost" onClick={() => setOpen(!open)} aria-expanded={open}>Esqueci minha senha</button>
      {open && (
        <div className="dk-card" style={{ textAlign: "left", marginTop: 8 }}>
          <p style={{ marginTop: 0 }}>Por segurança, a senha só é redefinida por quem tem acesso ao servidor. No Terminal do Mac:</p>
          <code style={{ display: "block", whiteSpace: "pre-wrap", fontSize: 12, padding: 10, background: "var(--app-bg)", borderRadius: 8 }}>
            cd ~/code/AI_Commerce_v2_Kit_Desenvolvimento{"\n"}docker compose -f infra/compose/docker-compose.yml --env-file infra/env/dry_run.env exec api python -m aicommerce.manage set-password --email SEU_EMAIL
          </code>
          <p className="faint" style={{ marginBottom: 0 }}>A senha nova é pedida sem aparecer na tela. Depois, entre aqui com ela.</p>
        </div>
      )}
    </div>
  );
}

function useDeskSafe() { return useContext(DeskContext); }
