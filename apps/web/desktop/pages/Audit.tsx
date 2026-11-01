"use client";
// Histórico & auditoria (UXD-014): trilha imutável do backend com filtro por tipo de ação, busca por assunto,
// paginação e verificação da cadeia de hashes. Somente leitura — esta tela não altera nada.
import { History, ShieldCheck, ShieldX } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AuditRow } from "@/lib/api";
import { useDesk } from "../context";
import { DataTable, Skeleton } from "../table";
import { Empty } from "../ui";

// Prefixos reais gravados pelo backend (missão é uma tarefa do Atlas: criada como agent_task.*). Vários por grupo.
const GROUPS: [string, string][] = [
  ["", "Todas as ações"], ["approval.,proposal.,execution.", "Aprovações e execuções"],
  ["agent_task.,mission.,agent.", "Missões e tarefas de agentes"], ["listing.,channel.", "Anúncios e canais"],
  ["catalog.", "Catálogo"], ["order.,fiscal.,settlement.", "Pedidos, fiscal e repasses"], ["supplier.", "Fornecedores"],
  ["budget.,campaign.", "Orçamento e campanhas"], ["emergency_stop.", "Kill switch"], ["auth.,user.,security.", "Acessos e usuários"],
  ["gate.,go_live.", "Homologações"], ["kb.", "Base de conhecimento"], ["desktop.", "App desktop"],
];

export default function Audit() {
  const desk = useDesk();
  const [action, setAction] = useState("");
  const [subject, setSubject] = useState("");
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [chain, setChain] = useState<{ intact: boolean; first_invalid_id: number | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = desk.can("audit:read");

  const load = useCallback((before?: number) => {
    api.auditEvents(desk.token, { action, subject_id: subject.trim() || undefined, before_id: before, limit: 50 })
      .then((r) => { setRows((prev) => (before ? [...(prev ?? []), ...r.items] : r.items)); setNext(r.next_before_id); setError(null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Falha de conexão com a API"));
  }, [desk.token, action, subject]);
  useEffect(() => { if (allowed) load(); }, [load, allowed]);

  if (!allowed) return (
    <div className="page"><div className="page-head"><div><h1>Histórico & auditoria</h1></div></div>
      <Empty icon={<History />} title="Seu papel não lê a trilha de auditoria">Peça ao proprietário a permissão “Ler e verificar trilha de auditoria”.</Empty></div>
  );
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Histórico & auditoria</h1>
          <p>Registro imutável e encadeado de tudo que pessoas, agentes e o sistema fizeram.</p>
        </div>
        <div className="actions">
          {chain && (chain.intact
            ? <span className="dk-chip ok" role="status"><ShieldCheck aria-hidden="true" />Cadeia íntegra</span>
            : <span className="dk-chip crit" role="alert"><ShieldX aria-hidden="true" />Quebra no evento #{chain.first_invalid_id}</span>)}
          <button type="button" className="btn" onClick={() => api.auditVerify(desk.token).then(setChain).catch((e) => setError(String(e)))}>
            <ShieldCheck />Verificar integridade</button>
        </div>
      </div>
      <div className="filters">
        <select className="field" value={action} onChange={(e) => setAction(e.target.value)} aria-label="Tipo de ação">
          {GROUPS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input className="field" type="search" value={subject} placeholder="ID do assunto (pedido, proposta, missão…)" aria-label="Filtrar por ID do assunto"
          onChange={(e) => setSubject(e.target.value)} style={{ minWidth: 320 }} />
      </div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      <section className="dk-card table-card" aria-label="Eventos de auditoria">
        {rows === null ? <Skeleton /> : rows.length === 0 ? <Empty icon={<History />} title="Nenhum evento com esse filtro" /> : (
          <DataTable id="audit" label="Eventos de auditoria" rows={rows} rowKey={(r) => String(r.id)} selected={open === null ? null : String(open)}
            onSelect={(r) => setOpen(open === r.id ? null : r.id)} pageSize={50} initialSort={{ key: "id", dir: -1 }} columns={[
              { key: "id", label: "#", optional: true, hidden: true, sort: (r) => r.id, render: (r) => <span className="faint num">{r.id}</span> },
              { key: "when", label: "Quando", sort: (r) => r.id, render: (r) => <span className="num faint">{new Date(r.occurred_at).toLocaleString("pt-BR")}</span> },
              { key: "who", label: "Quem", sort: (r) => r.actor_label, render: (r) => <span className="ellipsis" style={{ display: "inline-block", maxWidth: 220 }}><span className="faint">{r.actor_type}</span> {r.actor_label}</span> },
              { key: "action", label: "Ação", sort: (r) => r.action, render: (r) => <code>{r.action}</code> },
              { key: "subject", label: "Assunto", optional: true, sort: (r) => r.subject_type, render: (r) => <>{r.subject_type} <span className="faint">{r.subject_id.slice(0, 12)}</span></> },
              { key: "mode", label: "Modo", optional: true, sort: (r) => r.mode, render: (r) => r.mode === "DRY_RUN" ? <span className="dk-chip demo">Simulado</span> : <span className="dk-chip">{r.mode}</span> },
            ]} />
        )}
        {open !== null && rows?.find((r) => r.id === open) && (
          <div style={{ padding: "0 10px 10px" }} aria-label="Detalhes do evento">
            <h3 className="sec-title">Evento #{open}</h3>
            <pre className="code-block">{JSON.stringify((({ id, subject_id, payload }) => ({ id, subject_id, payload }))(rows.find((r) => r.id === open)!), null, 2)}</pre>
          </div>
        )}
        {next && <div style={{ textAlign: "center", marginTop: 12 }}><button type="button" className="btn" onClick={() => load(next)}>Carregar mais antigos</button></div>}
      </section>
    </div>
  );
}
