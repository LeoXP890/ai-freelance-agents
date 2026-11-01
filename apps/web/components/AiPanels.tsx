"use client";
// IA no escritório: habilidades reais dos agentes (tarefas com LLM), chat contextual (somente leitura),
// missões do Atlas, oportunidades da Luna e custo de IA. Tudo vem da API; "Simulado" quando o LLM é o mock.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  api, ApiError, type AgentTask, type AiSpec, type AiUsage, type ChatReply, type Listing, type Mission,
  type OpportunityRow, type OrderSummary,
} from "@/lib/api";
import { formatMicroUsd, formatMinor, SKILL_INPUT } from "@/lib/format";

const msg = (e: unknown) => (e instanceof ApiError ? `${e.status}: ${e.message}` : "Falha de conexão com a API");

export function AgentSkills({ token, agentId, canCreate, refreshSignal }: {
  token: string; agentId: string; canCreate: boolean; refreshSignal: number;
}) {
  const [specs, setSpecs] = useState<AiSpec[]>([]);
  const [provider, setProvider] = useState("");
  const [kind, setKind] = useState("");
  const [text, setText] = useState("");
  const [ref, setRef] = useState("");
  const [listings, setListings] = useState<Listing[]>([]);
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    api.aiSpecs(token).then((r) => {
      const mine = r.items.filter((s) => s.agent_id === agentId && s.kind !== "mission.plan");
      setSpecs(mine); setProvider(r.provider); setKind(mine[0]?.kind ?? "");
    }).catch((e) => setError(msg(e)));
  }, [token, agentId]);
  const need = SKILL_INPUT[kind] ?? "none";
  useEffect(() => {
    setRef("");
    if (need === "listing" || need === "offer") api.listings(token).then((r) => setListings(r.items)).catch(() => undefined);
    if (need === "order") api.orders(token).then((r) => setOrders(r.items)).catch(() => undefined);
  }, [need, token, refreshSignal]);

  if (!specs.length || !canCreate) return null;

  async function run(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setDone(null);
    const input: Record<string, unknown> = {};
    if (need === "topic") input.topic = text;
    if (need === "question") input.question = text;
    if (need === "category") input.category = text;
    if (need === "listing") input.listing_id = ref;
    if (need === "order") { input.order_id = ref; input.customer_message = text; }
    if (need === "offer") input.offer_id = listings.find((l) => l.id === ref)?.offer_id;
    try {
      const t = await api.createTask(token, agentId, kind, input);
      setDone(`Tarefa ${t.id.slice(0, 8)} na fila — acompanhe em "Tarefas recentes".`);
      setText("");
    } catch (err) { setError(msg(err)); } finally { setBusy(false); }
  }

  const needsText = need === "topic" || need === "question" || need === "category" || need === "order";
  const needsRef = need === "listing" || need === "order" || need === "offer";
  return (
    <form className="dept" onSubmit={run} aria-label="Habilidades do agente">
      <h3>Habilidades {provider === "mock" && <span className="sim">LLM simulado</span>}</h3>
      <select aria-label="Tarefa" value={kind} onChange={(e) => setKind(e.target.value)}>
        {specs.map((s) => <option key={s.kind} value={s.kind}>{s.title}{s.tier === "advanced" ? " (modelo avançado)" : ""}</option>)}
      </select>
      {needsRef && (
        <select aria-label="Referência" value={ref} onChange={(e) => setRef(e.target.value)} required>
          <option value="">{need === "order" ? "Selecione o pedido…" : "Selecione o anúncio…"}</option>
          {need === "order" ? orders.map((o) => <option key={o.id} value={o.id}>{o.external_id} · {o.status}</option>)
            : listings.map((l) => <option key={l.id} value={l.id}>{l.title} · {l.channel}</option>)}
        </select>
      )}
      {needsText && (
        <input aria-label="Entrada" required={need !== "category"} value={text} onChange={(e) => setText(e.target.value)}
          placeholder={need === "topic" ? "Tema (ex.: garrafa térmica)" : need === "question" ? "Pergunta de compliance"
            : need === "order" ? "Mensagem do cliente" : "Categoria (opcional)"} />
      )}
      <button type="submit" className="primary" disabled={busy || (needsRef && !ref)}>{busy ? "Enviando…" : "Executar"}</button>
      {done && <p className="good small">{done}</p>}
      {error && <p className="err small" role="alert">{error}</p>}
    </form>
  );
}

export function TaskResult({ t }: { t: AgentTask }) {
  const r = t.result as Record<string, unknown> | null;
  if (!r) return null;
  const summary = (r.summary ?? r.headline ?? r.answer ?? r.reply) as string | undefined;
  return (
    <div className="small muted">
      {summary && <div>{String(summary).slice(0, 220)}</div>}
      {typeof r.model === "string" && <div>modelo {r.model}{r.llm_simulated ? " (simulado)" : ""}
        {r.cache_hit ? " · cache" : ""}{typeof r.proposal_id === "string" ? " · proposta criada" : ""}</div>}
    </div>
  );
}

export function AgentChat({ token, agentId }: { token: string; agentId: string }) {
  const [conv, setConv] = useState<string | undefined>();
  const [items, setItems] = useState<{ role: "user" | "agent"; text: string; meta?: ChatReply }[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(e: FormEvent) {
    e.preventDefault();
    const m = text.trim();
    if (m.length < 2) return;
    setBusy(true); setError(null); setText("");
    setItems((x) => [...x, { role: "user", text: m }]);
    try {
      const r = await api.chat(token, agentId, m, conv);
      setConv(r.conversation_id);
      setItems((x) => [...x, { role: "agent", text: r.answer, meta: r }]);
    } catch (err) { setError(msg(err)); } finally { setBusy(false); }
  }

  return (
    <div className="dept chat" aria-label="Chat com o agente">
      <h3>Conversar</h3>
      <ol className="chat-log" aria-live="polite">
        {items.map((it, i) => (
          <li key={i} className={it.role}>
            <div>{it.text}</div>
            {it.meta && (
              <div className="muted small">
                rota {it.meta.route} · {it.meta.model}{it.meta.llm_simulated ? " (simulado)" : ""} · {formatMicroUsd(it.meta.cost_micro_usd)}
                {it.meta.sources.length > 0 && <> · fontes: {it.meta.sources.map((s) => s.title).join("; ")}</>}
                {it.meta.suggested_action.action_type !== "NONE" && (
                  <div className="warn">Sugestão: {it.meta.suggested_action.action_type} — exige proposta e aprovação.</div>)}
              </div>
            )}
          </li>
        ))}
      </ol>
      <form onSubmit={send} className="row">
        <input aria-label="Mensagem" value={text} onChange={(e) => setText(e.target.value)} maxLength={4000}
          placeholder="Pergunte algo ao agente…" disabled={busy} />
        <button type="submit" disabled={busy || text.trim().length < 2}>{busy ? "…" : "Enviar"}</button>
      </form>
      <p className="muted small">Somente leitura: o agente informa e sugere; nada é executado pelo chat.</p>
      {error && <p className="err small" role="alert">{error}</p>}
    </div>
  );
}

export function AtlasMissions({ token, refreshSignal }: { token: string; refreshSignal: number }) {
  const [goal, setGoal] = useState("");
  const [missionId, setMissionId] = useState<string | null>(null);
  const [mission, setMission] = useState<Mission | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!missionId) return;
    try { setMission(await api.mission(token, missionId)); } catch (e) { setError(msg(e)); }
  }, [token, missionId]);
  useEffect(() => { void load(); }, [load, refreshSignal]);

  async function start(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try { setMissionId((await api.createMission(token, goal)).mission_id); setGoal(""); } catch (err) { setError(msg(err)); }
  }
  return (
    <div className="dept">
      <h3>Missão multiagente</h3>
      <form onSubmit={start} className="row">
        <input aria-label="Objetivo da missão" value={goal} onChange={(e) => setGoal(e.target.value)} minLength={5}
          placeholder="Ex.: avaliar nicho de casa e cozinha" required />
        <button type="submit" className="primary">Iniciar</button>
      </form>
      {mission && (
        <div className="job small">
          <strong>{mission.state}</strong> · custo {formatMicroUsd(mission.cost_micro_usd)}
          <ul className="list">{mission.children.map((c) => (
            <li key={c.id}>{c.agent_id} · {c.kind}: {c.state}{c.summary ? ` — ${c.summary.slice(0, 90)}` : ""}</li>))}</ul>
          {mission.result?.summary && <div>{mission.result.summary}</div>}
          {mission.error && <div className="err">{mission.error}</div>}
        </div>
      )}
      {error && <p className="err small" role="alert">{error}</p>}
    </div>
  );
}

export function LunaOpportunities({ token, canPropose, refreshSignal, onProposed }: {
  token: string; canPropose: boolean; refreshSignal: number; onProposed(): void;
}) {
  const [items, setItems] = useState<OpportunityRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setItems((await api.opportunities(token)).items); } catch (e) { setError(msg(e)); }
  }, [token]);
  useEffect(() => { void load(); }, [load, refreshSignal]);
  return (
    <div className="dept">
      <h3>Oportunidades</h3>
      {items.length === 0 ? <p className="muted small">Nenhuma ainda — execute uma pesquisa acima.</p> : (
        <ul className="inbox">{items.slice(0, 12).map((o) => {
          const m = o.sources.find((s) => s.market)?.market;
          return (
          <li key={o.id} className="inbox-item static">
            {m && m.pictures.length > 0 && (
              <span className="opp-pics">{m.pictures.slice(0, 3).map((src, i) => (
                <img key={src} src={src} alt={i === 0 ? `Foto do produto: ${o.title}` : ""} width={72} height={72}
                  loading="lazy" referrerPolicy="no-referrer" />))}</span>)}
            <span className="inbox-title">{o.title} <span className="muted small">· nota {o.score}</span></span>
            {m && (
              <span className="small">
                {m.price_median != null && <>preço típico {formatMinor(Math.round(m.price_median * 100))} ({m.sellers} vendedor{m.sellers === 1 ? "" : "es"}) · </>}
                {m.fee?.percentage_fee != null && <>tarifa {m.fee.listing_type} {m.fee.percentage_fee}% · </>}
                {m.permalink && <a href={m.permalink} target="_blank" rel="noreferrer noopener">ver no Mercado Livre</a>}
              </span>)}
            {m?.shipping && (
              <span className="small">
                frete por sua conta {formatMinor(Math.round(m.shipping.seller_cost * 100))}
                {m.max_supplier_and_tax != null && <> · <strong>sobra até {formatMinor(Math.round(m.max_supplier_and_tax * 100))}</strong> para fornecedor, imposto e lucro</>}
              </span>)}
            <span className="small">{o.rationale}</span>
            <span className="inbox-meta"><span>evidência {o.evidence_quality}</span>
              <span className="muted">{o.sources.map((s) => s.id).join(", ")}</span>
              {o.simulated && <span className="sim">Simulado</span>}<span>{o.status}</span></span>
            {o.contraindications.length > 0 && <span className="warn small">{o.contraindications.join("; ")}</span>}
            {canPropose && o.status === "NEW" && (
              <button type="button" onClick={async () => {
                try { await api.opportunityProposal(token, o.id); await load(); onProposed(); } catch (e) { setError(msg(e)); }
              }}>Propor rascunho de anúncio</button>)}
          </li>);
        })}</ul>
      )}
      {error && <p className="err small" role="alert">{error}</p>}
    </div>
  );
}

export function AiCost({ token, refreshSignal }: { token: string; refreshSignal: number }) {
  const [u, setU] = useState<AiUsage | null>(null);
  useEffect(() => { api.aiUsage(token).then(setU).catch(() => undefined); }, [token, refreshSignal]);
  if (!u) return null;
  const pct = Math.round((u.month_spend_micro_usd / Math.max(1, u.monthly_budget_micro_usd)) * 100);
  return (
    <div className="dept">
      <h3>Custo de IA (mês) {u.provider === "mock" && <span className="sim">mock</span>}</h3>
      <div className="small">{formatMicroUsd(u.month_spend_micro_usd)} de {formatMicroUsd(u.monthly_budget_micro_usd)} ({pct}%)</div>
      <div className="bar" aria-hidden="true"><div className="fill" style={{ width: `${Math.min(100, pct)}%` }} /></div>
      <ul className="list">{u.by_agent_model.slice(0, 8).map((r) => (
        <li key={r.agent_id + r.model}>{r.agent_id} · {r.model}: {r.calls} chamada(s) · {formatMicroUsd(r.cost_micro_usd)}</li>))}</ul>
    </div>
  );
}
