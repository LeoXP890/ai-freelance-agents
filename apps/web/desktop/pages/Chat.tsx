"use client";
// UX-03 Conversas com agentes (UXD-009): lista de conversas à esquerda, histórico no centro, contexto à direita.
// Mensagem humana, resposta do agente, fontes/dados usados, ação sugerida e eventos do sistema são componentes
// distintos. O chat só informa e sugere: ação real vira proposta. Sem raciocínio interno bruto, sem tempo prometido.
import { BookOpen, ClipboardCopy, Database, Download, FileSearch, Inbox, ListChecks, MessageSquarePlus, Search, Send, ShieldQuestion, Sparkles } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type ChatReply } from "@/lib/api";
import { lookFor, since } from "@/lib/agentIdentity";
import { formatMicroUsd } from "@/lib/format";
import AgentPortrait from "../AgentPortrait";
import { useDesk } from "../context";
import { Empty, StateChip } from "../ui";

interface ConvRow { id: string; agent_id: string; agents: string[]; title: string; last_at: string; messages: number; cost_micro_usd: number }
interface Msg { key: string; role: "user" | "assistant" | "system"; agent: string; text: string; at: string; reply?: ChatReply; meta?: Record<string, unknown> }
const ROUTE: Record<string, string> = { sql: "consultou dados do sistema", retrieval: "buscou na base de conhecimento", rule: "aplicou regras da política", general: "resposta geral" };
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export default function Chat() {
  const desk = useDesk();
  const [convs, setConvs] = useState<ConvRow[] | null>(null);
  const [q, setQ] = useState("");
  const [agent, setAgent] = useState<string>(desk.chatTarget?.agentId ?? "atlas");
  const [conv, setConv] = useState<string | null>(desk.chatTarget?.conversationId ?? null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [older, setOlder] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSources, setShowSources] = useState(false);
  const log = useRef<HTMLOListElement>(null);
  const agents = desk.office.order.map((id) => desk.office.agents[id]!).filter(Boolean);
  const name = (id: string) => desk.office.agents[id]?.name ?? id;

  const loadList = useCallback(() => { api.conversations(desk.token).then((r) => setConvs(r.items)).catch(() => setConvs([])); }, [desk.token]);
  useEffect(() => { if (desk.me && desk.can("ai:use")) loadList(); }, [loadList, desk.me]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { // pedido vindo do workspace do agente ("Conversar")
    if (desk.chatTarget) { setAgent(desk.chatTarget.agentId); setConv(desk.chatTarget.conversationId ?? null); if (!desk.chatTarget.conversationId) setMsgs([]); }
  }, [desk.chatTarget]);
  const openConv = useCallback(async (id: string, before?: number) => {
    try {
      const r = await api.conversation(desk.token, id, before);
      const rows: Msg[] = r.items.map((m) => ({ key: `s${m.id}`, role: m.role === "user" ? "user" : "assistant", agent: m.agent_id ?? r.agent_id ?? agent, text: m.content, at: m.at, meta: m.meta }));
      setMsgs((cur) => (before ? [...rows, ...cur] : rows)); setOlder(r.next_before_id); setConv(id); setError(null);
      if (!before && r.agent_id) setAgent(r.agent_id);
    } catch (e) { setError(msg(e)); }
  }, [desk.token, agent]);
  useEffect(() => { if (conv && msgs.length === 0) void openConv(conv); }, [conv]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { log.current?.lastElementChild?.scrollIntoView({ block: "end" }); }, [msgs.length, busy]);

  async function send(e?: FormEvent, preset?: string) {
    e?.preventDefault();
    const m = (preset ?? text).trim();
    if (m.length < 2 || busy) return;
    setBusy(true); setError(null); setText("");
    const lastAgent = [...msgs].reverse().find((x) => x.role === "assistant")?.agent;
    const add: Msg[] = [];
    if (conv && lastAgent && lastAgent !== agent) add.push({ key: `sys${Date.now()}`, role: "system", agent, text: `Conversa continuada com ${name(agent)}`, at: new Date().toISOString() });
    add.push({ key: `u${Date.now()}`, role: "user", agent, text: m, at: new Date().toISOString() });
    setMsgs((x) => [...x, ...add]);
    try {
      const r = await api.chat(desk.token, agent, m, conv ?? undefined);
      setConv(r.conversation_id);
      setMsgs((x) => [...x, { key: `a${Date.now()}`, role: "assistant", agent, text: r.answer, at: new Date().toISOString(), reply: r }]);
      loadList();
    } catch (err) { setError(msg(err)); } finally { setBusy(false); }
  }
  function newConv(a = agent) { setConv(null); setMsgs([]); setOlder(null); setAgent(a); setError(null); }
  const transcript = () => msgs.map((x) => `${x.role === "user" ? "Você" : x.role === "system" ? "Sistema" : name(x.agent)} (${new Date(x.at).toLocaleString("pt-BR")}):\n${x.text}`).join("\n\n");
  const lastReply = [...msgs].reverse().find((x) => x.role === "assistant");
  const shown = useMemo(() => (convs ?? []).filter((c) => !q || `${c.title} ${c.agents.map(name).join(" ")}`.toLowerCase().includes(q.toLowerCase())), [convs, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const a = desk.office.agents[agent];
  const convCost = convs?.find((c) => c.id === conv)?.cost_micro_usd;

  if (desk.me && !desk.can("ai:use")) return <div className="page"><Empty icon={<MessageSquarePlus />} title="Seu papel não conversa com agentes" /></div>;
  return (
    <div className="chat-page">
      <aside className="chat-list" aria-label="Conversas">
        <div className="chat-list-head">
          <button type="button" className="btn primary" style={{ width: "100%" }} onClick={() => newConv()}><MessageSquarePlus />Nova conversa</button>
          <label className="search-field"><Search size={16} aria-hidden="true" /><input className="field" type="search" placeholder="Buscar conversas" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar conversas" /></label>
        </div>
        <ul>
          {convs === null ? <li className="faint" style={{ padding: 12 }}>Carregando…</li> : shown.length === 0 ? <li className="faint" style={{ padding: 12 }}>Nenhuma conversa ainda.</li> : shown.map((c) => (
            <li key={c.id}><button type="button" aria-current={conv === c.id ? "true" : undefined} onClick={() => { setMsgs([]); void openConv(c.id); }}>
              <AgentPortrait agentId={c.agent_id} size={28} />
              <span className="grow" style={{ minWidth: 0 }}><strong className="ellipsis" style={{ display: "block" }}>{c.title || "(sem título)"}</strong>
                <span className="faint">{c.agents.map(name).join(", ")} · {since(c.last_at)}</span></span>
            </button></li>))}
        </ul>
      </aside>

      <section className="chat-main" aria-label="Histórico da conversa">
        <header className="chat-head">
          <div className="row-head">
            <span className="faint">Falar com</span>
            <select className="field" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agente">
              {agents.map((x) => <option key={x.agent_id} value={x.agent_id}>{x.name} — {lookFor(x.agent_id).short}</option>)}
            </select>
            <span className="grow" />
            <button type="button" className="btn ghost" disabled={!msgs.length} onClick={() => { void navigator.clipboard?.writeText(transcript()).then(() => desk.notify("Conversa copiada.")); }}><ClipboardCopy />Copiar</button>
            <button type="button" className="btn ghost" disabled={!msgs.length} onClick={() => {
              const url = URL.createObjectURL(new Blob([`# Conversa com ${name(agent)}\n\n${transcript()}\n`], { type: "text/markdown" }));
              const el = document.createElement("a"); el.href = url; el.download = `conversa-${(conv ?? "nova").slice(0, 8)}.md`; el.click(); URL.revokeObjectURL(url);
            }}><Download />Exportar</button>
          </div>
        </header>
        <ol className="chat-log" ref={log} aria-live="polite">
          {older && <li className="msg-sys"><button type="button" className="dk-link" onClick={() => conv && openConv(conv, older)}>Carregar mensagens anteriores</button></li>}
          {msgs.length === 0 && !busy && (
            <li className="msg-sys"><Empty icon={<Sparkles />} title={`Converse com ${name(agent)}`}>O agente responde com dados do sistema e fontes da base de conhecimento. Ele só informa e sugere — nada é executado pelo chat.</Empty></li>
          )}
          {msgs.map((m) => m.role === "system" ? <li key={m.key} className="msg-sys"><span className="faint">— {m.text} —</span></li> : m.role === "user" ? (
            <li key={m.key} className="msg-me"><div className="bubble">{m.text}</div><time className="faint">{new Date(m.at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</time></li>
          ) : (
            <li key={m.key} className="msg-agent">
              <AgentPortrait agentId={m.agent} size={30} />
              <div className="agent-body">
                <div className="who"><strong>{name(m.agent)}</strong> <span className="faint">{ROUTE[String(m.reply?.route ?? m.meta?.route ?? "")] ?? ""}{m.reply?.llm_simulated ? " · modelo simulado" : ""}</span></div>
                <div className="bubble">{m.text}</div>
                {m.reply && Object.keys(m.reply.facts).length > 0 && (
                  <details className="evidence"><summary><Database size={14} />Dados usados ({Object.keys(m.reply.facts).length})</summary>
                    <dl className="dk-kv">{Object.entries(m.reply.facts).map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k.replace(/^F_/, "")}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>)}</dl></details>
                )}
                {m.reply && m.reply.sources.length > 0 && (
                  <details className="evidence" open={showSources && m === lastReply}><summary><BookOpen size={14} />Fontes ({m.reply.sources.length})</summary>
                    <ul>{m.reply.sources.map((s) => <li key={s.id}><strong>{s.title}</strong> <span className={`dk-chip ${s.approval_status === "APPROVED" ? "ok" : "warn"}`}>{s.approval_status === "APPROVED" ? "aprovada" : "não revisada"}</span><div className="faint ellipsis">{s.url}</div></li>)}</ul></details>
                )}
                {!m.reply && Array.isArray(m.meta?.source_ids) && (m.meta!.source_ids as string[]).length > 0 && <span className="faint">{(m.meta!.source_ids as string[]).length} fonte(s) citada(s)</span>}
                {m.reply && m.reply.suggested_action.action_type !== "NONE" && (
                  <div className="callout warn suggested"><ShieldQuestion size={16} aria-hidden="true" /> <strong>Ação sugerida:</strong> {m.reply.suggested_action.description || m.reply.suggested_action.action_type}
                    <div className="faint">Não foi executada. Ações reais viram proposta e dependem de aprovação.</div>
                    {desk.can("approvals:read") && <button type="button" className="dk-link" onClick={() => desk.go("approvals")}><Inbox size={14} />Ver Central de Aprovações</button>}</div>
                )}
                <div className="faint" style={{ fontSize: 11 }}>{new Date(m.at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}{(m.reply?.model ?? m.meta?.model) ? ` · ${m.reply?.model ?? m.meta?.model}` : ""}
                  {(m.reply?.cost_micro_usd ?? m.meta?.cost_micro_usd) !== undefined ? ` · ${formatMicroUsd(Number(m.reply?.cost_micro_usd ?? m.meta?.cost_micro_usd))}` : ""}</div>
              </div>
            </li>
          ))}
          {busy && <li className="msg-agent pending" role="status"><AgentPortrait agentId={agent} size={30} /><div className="bubble faint">{name(agent)} está consultando dados e fontes…</div></li>}
        </ol>
        {error && <p role="alert" className="dk-error" style={{ padding: "0 16px" }}>{error}</p>}
        <div className="chat-commands" role="group" aria-label="Comandos">
          <button type="button" className="btn ghost" disabled={busy || !desk.online} onClick={() => setText("Faça uma análise com números e fontes sobre: ")}><Sparkles />Pedir análise</button>
          <button type="button" className="btn ghost" disabled={!lastReply?.reply?.sources.length} onClick={() => setShowSources((v) => !v)}><FileSearch />Ver fonte</button>
          <button type="button" className="btn ghost" onClick={() => desk.openAgent(agent)}><ListChecks />Abrir tarefas</button>
          {desk.can("tasks:create") && <button type="button" className="btn ghost" disabled={!lastReply || !desk.online} onClick={async () => {
            try { await api.createTask(desk.token, "clara", "compliance.review", { question: `Revisar a resposta de ${name(lastReply!.agent)}: ${lastReply!.text.slice(0, 900)}` }); desk.notify("Revisão pedida à Clara (compliance) — acompanhe nas tarefas dela."); }
            catch (e) { setError(msg(e)); }
          }}><ShieldQuestion />Solicitar revisão</button>}
        </div>
        <form className="chat-compose" onSubmit={send}>
          <textarea className="field" rows={2} value={text} onChange={(e) => setText(e.target.value)} maxLength={4000} disabled={busy}
            placeholder={`Pergunte algo a ${name(agent)}… (Enter envia, Shift+Enter quebra linha)`} aria-label="Mensagem"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
          <button type="submit" className="btn primary" disabled={busy || text.trim().length < 2 || !desk.online}><Send />{busy ? "Aguardando…" : "Enviar"}</button>
        </form>
      </section>

      <aside className="chat-context" aria-label="Contexto do agente">
        {a ? (
          <>
            <div style={{ textAlign: "center" }}><AgentPortrait agentId={agent} size={64} /><h2 style={{ fontSize: 17, marginTop: 6 }}>{a.name}</h2><div className="muted">{lookFor(agent).short}</div>
              <div style={{ marginTop: 8 }}><StateChip state={a.state} /></div></div>
            <dl className="dk-kv" style={{ gridTemplateColumns: "1fr" }}>
              <dt>Departamento</dt><dd>{lookFor(agent).department}</dd>
              <dt>Atualizado</dt><dd>{since(a.last_event_at)}</dd>
              {conv && <><dt>Esta conversa</dt><dd>{convCost !== undefined ? `custo de IA ${formatMicroUsd(convCost)}` : "nova"}</dd></>}
            </dl>
            <button type="button" className="btn" onClick={() => desk.openAgent(agent)}>Abrir agente</button>
            <p className="faint" style={{ margin: 0 }}>O agente não executa nada pelo chat: publicar, gastar ou reembolsar sempre passa por proposta e aprovação humana.</p>
          </>
        ) : <p className="muted">Agente indisponível.</p>}
      </aside>
    </div>
  );
}
