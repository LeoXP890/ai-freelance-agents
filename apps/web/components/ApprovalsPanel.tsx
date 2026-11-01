"use client";
// Sala do CEO / caixa de aprovações (SDD §8, §24.1 "aprovações ↔ sala CEO").
// A decisão envia o payload_sha256 e a versão EXIBIDOS: se a proposta mudou, o backend recusa.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ApprovalDetail, type DecisionAction, type Proposal } from "@/lib/api";
import { ACTION_LABEL, approvalErrorMessage, formatMinor, shortHash, STATUS_LABEL } from "@/lib/format";

const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

type Tab = "PENDING_APPROVAL" | "ALL";

export default function ApprovalsPanel({ token, canDecide, refreshSignal, onClose }: {
  token: string; canDecide: boolean; refreshSignal: number; onClose(): void;
}) {
  const [tab, setTab] = useState<Tab>("PENDING_APPROVAL");
  const [items, setItems] = useState<Proposal[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ApprovalDetail | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<DecisionAction | "revoke" | null>(null);
  // Mesma chave para repetições da MESMA decisão (duplo clique / rede instável) → backend responde replay.
  const keys = useRef<Record<string, string>>({});

  const loadList = useCallback(async () => {
    try {
      const r = await api.approvals(token, tab === "ALL" ? undefined : tab);
      setItems(r.items);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha de conexão com a API");
    }
  }, [token, tab]);

  const loadDetail = useCallback(async (id: string) => {
    try {
      setDetail(await api.approval(token, id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha de conexão com a API");
    }
  }, [token]);

  useEffect(() => { void loadList(); }, [loadList, refreshSignal]);
  useEffect(() => { if (selected) void loadDetail(selected); }, [selected, loadDetail, refreshSignal]);

  function open(id: string) {
    setSelected(id);
    setDetail(null);
    setComment("");
    setError(null);
    setConfirming(null);
  }

  async function act(action: DecisionAction | "revoke") {
    if (!detail) return;
    const slot = `${detail.id}:${action}`;
    keys.current[slot] ??= newKey();
    setBusy(true);
    setError(null);
    try {
      const updated = action === "revoke"
        ? await api.revoke(token, detail.id, comment || "revogada pelo decisor", keys.current[slot])
        : await api.decide(token, detail.id, action, {
          payload_sha256: detail.payload_sha256, subject_version: detail.subject_version, comment,
          idempotency_key: keys.current[slot],
        });
      setDetail(updated);
      setConfirming(null);
      setComment("");
      void loadList();
    } catch (e) {
      if (e instanceof ApiError) {
        setError(approvalErrorMessage(e.code, e.message));
        if (e.code === "INVALID_STATE" || e.code === "STALE_PAYLOAD") void loadDetail(detail.id);
      } else {
        setError("Falha de conexão — tente de novo (a mesma decisão não será duplicada).");
      }
    } finally {
      setBusy(false);
    }
  }

  const needsComment = confirming === "reject" || confirming === "request-changes";

  return (
    <aside className="panel approvals" aria-label="Caixa de aprovações da sala do CEO">
      <header className="panel-head">
        <div>
          <div className="muted small">SALA DO CEO</div>
          <h2>Aprovações</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="Fechar aprovações">✕</button>
      </header>

      {!detail ? (
        <>
          <div className="seg" role="tablist" aria-label="Filtro de propostas">
            <button type="button" role="tab" aria-selected={tab === "PENDING_APPROVAL"}
              aria-pressed={tab === "PENDING_APPROVAL"} onClick={() => setTab("PENDING_APPROVAL")}>Pendentes</button>
            <button type="button" role="tab" aria-selected={tab === "ALL"} aria-pressed={tab === "ALL"}
              onClick={() => setTab("ALL")}>Todas</button>
          </div>
          {error && <p className="err" role="alert">{error}</p>}
          {items.length === 0 ? (
            <p className="muted small">{tab === "ALL" ? "Nenhuma proposta ainda." : "Nada aguardando sua decisão."}</p>
          ) : (
            <ul className="inbox">
              {items.map((p) => (
                <li key={p.id}>
                  <button type="button" className="inbox-item" onClick={() => open(p.id)}>
                    <span className="inbox-title">{ACTION_LABEL[p.action_type] ?? p.action_type}</span>
                    <span className="muted small">{p.agent_id ?? "humano"} · {p.subject_type}:{p.subject_id}</span>
                    <span className="inbox-meta">
                      <span className={`status s-${p.status}`}>{STATUS_LABEL[p.status] ?? p.status}</span>
                      {p.monetary_scope_minor > 0 && <span>{formatMinor(p.monetary_scope_minor, p.currency)}</span>}
                      {p.simulated && <span className="sim">Simulado</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <div className="approval-detail">
          <button type="button" className="link" onClick={() => { setDetail(null); setSelected(null); }}>
            ← Voltar à lista
          </button>
          <h3>{ACTION_LABEL[detail.action_type] ?? detail.action_type}
            {detail.simulated && <span className="sim">Simulado</span>}</h3>
          <div className={`status s-${detail.status}`}>{STATUS_LABEL[detail.status] ?? detail.status}</div>
          <dl className="kv">
            <dt>Agente</dt><dd>{detail.agent_id ?? "—"}</dd>
            <dt>Assunto</dt><dd>{detail.subject_type}:{detail.subject_id} (v{detail.subject_version})</dd>
            <dt>Risco</dt><dd>{detail.risk_score}/100</dd>
            <dt>Valor</dt><dd>{detail.monetary_scope_minor > 0
              ? formatMinor(detail.monetary_scope_minor, detail.currency) : "sem gasto"}</dd>
            <dt>Canal</dt><dd>{detail.go_live_context?.channel ?? "—"}</dd>
            <dt>Política</dt><dd>{detail.policy_version} → {detail.policy_decision}</dd>
            <dt>Hash</dt><dd><code title={detail.payload_sha256}>{shortHash(detail.payload_sha256)}</code></dd>
            <dt>Expira</dt><dd>{new Date(detail.expires_at).toLocaleString("pt-BR")}</dd>
          </dl>
          {detail.policy_reasons.length > 0 && (
            <ul className="reasons">{detail.policy_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
          <h4>Conteúdo que será executado</h4>
          <pre className="payload">{JSON.stringify(detail.payload, null, 2)}</pre>
          <div className="muted small">Evidências: {detail.evidence_refs.join(", ") || "nenhuma"}</div>
          {!detail.executable && (
            <p className="warn small">Esta ação ainda não tem adaptador: se aprovada, a execução falhará como
              UNSUPPORTED (nada é simulado como sucesso).</p>
          )}

          {detail.job && (
            <div className="job">
              <strong>Execução</strong>: {detail.job.status} · tentativas {detail.job.attempts}
              {detail.job.provider_ref && <> · ref <code>{detail.job.provider_ref}</code></>}
              {detail.job.error_kind && <div className="err small">{detail.job.error_kind}: {detail.job.last_error}</div>}
            </div>
          )}
          {detail.decisions.length > 0 && (
            <ul className="list">
              {detail.decisions.map((d) => (
                <li key={d.decided_at + d.decision}>{d.decision} · {new Date(d.decided_at).toLocaleString("pt-BR")}
                  {d.comment && <> — “{d.comment}”</>}</li>
              ))}
            </ul>
          )}

          {error && <p className="err" role="alert">{error}</p>}

          {canDecide && detail.status === "PENDING_APPROVAL" && (
            confirming ? (
              <div className="confirm">
                {needsComment ? (
                  <label>Comentário (obrigatório)
                    <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3}
                      maxLength={2000} autoFocus />
                  </label>
                ) : (
                  <p className="small">Confirmar aprovação do conteúdo com hash <code>{shortHash(detail.payload_sha256)}</code>
                    {detail.monetary_scope_minor > 0 && <> e reserva de {formatMinor(detail.monetary_scope_minor)}</>}?</p>
                )}
                <div className="row">
                  <button type="button" className={confirming === "approve" ? "primary" : "danger"}
                    disabled={busy || (needsComment && comment.trim().length < 3)} onClick={() => act(confirming as DecisionAction)}>
                    {busy ? "Enviando…" : confirming === "approve" ? "Confirmar aprovação"
                      : confirming === "reject" ? "Confirmar rejeição" : "Enviar pedido"}
                  </button>
                  <button type="button" disabled={busy} onClick={() => setConfirming(null)}>Cancelar</button>
                </div>
              </div>
            ) : (
              <div className="row actions">
                <button type="button" className="primary" onClick={() => setConfirming("approve")}>Aprovar</button>
                <button type="button" onClick={() => setConfirming("request-changes")}>Pedir mudanças</button>
                <button type="button" className="danger" onClick={() => setConfirming("reject")}>Rejeitar</button>
              </div>
            )
          )}
          {canDecide && detail.status === "AUTHORIZED" && detail.job && ["QUEUED", "RETRY"].includes(detail.job.status) && (
            confirming === "revoke" ? (
              <div className="confirm">
                <label>Motivo da revogação
                  <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} autoFocus />
                </label>
                <div className="row">
                  <button type="button" className="danger" disabled={busy || comment.trim().length < 3}
                    onClick={() => act("revoke")}>Confirmar revogação</button>
                  <button type="button" disabled={busy} onClick={() => setConfirming(null)}>Cancelar</button>
                </div>
              </div>
            ) : (
              <button type="button" className="danger" onClick={() => setConfirming("revoke")}>Revogar antes da execução</button>
            )
          )}
          {!canDecide && detail.status === "PENDING_APPROVAL" && (
            <p className="muted small">Somente o proprietário (com MFA) decide propostas.</p>
          )}
        </div>
      )}
    </aside>
  );
}
