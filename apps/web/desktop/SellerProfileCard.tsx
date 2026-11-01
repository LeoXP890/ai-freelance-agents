"use client";
// Perfil do vendedor (ADR 0006): tipo pretendido (Pessoa Física por padrão), situação da análise e o que falta.
// O CPF NÃO é pedido nem guardado. Trocar PF↔PJ é decisão do proprietário com MFA e derruba as homologações
// dependentes do perfil (o servidor faz isso e diz quais).
import { IdCard } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type SellerProfile } from "@/lib/api";
import { GATE_STATE } from "@/lib/format";
import { ELIG, type EligibilityRow, SELLER_SHORT } from "@/lib/seller";
import { useDesk } from "./context";
import { ConfirmDialog } from "./ui";
import { channelName } from "./pages/Finance";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

export default function SellerProfileCard() {
  const desk = useDesk();
  const [p, setP] = useState<SellerProfile | null>(null);
  const [elig, setElig] = useState<EligibilityRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [switchTo, setSwitchTo] = useState<"PERSON" | "COMPANY" | null>(null);
  const owner = desk.can("gates:approve");
  const load = useCallback(() => {
    api.sellerProfile(desk.token).then(setP).catch((e) => setError(msg(e)));
    api.eligibility(desk.token).then((r) => setElig(r.items)).catch(() => undefined);
  }, [desk.token]);
  const allowed = desk.can("golive:evaluate");
  useEffect(() => { if (allowed) load(); }, [load, allowed]);  // só quando o usuário/permissão muda (não a cada evento)
  if (!desk.can("golive:evaluate")) return null;
  const pending = [...new Set(elig.flatMap((e) => e.pending))];
  return (
    <section className="dk-card" aria-label="Perfil do vendedor">
      <div className="card-title"><IdCard size={16} />Perfil do vendedor</div>
      {error && <p role="alert" className="dk-error">{error}</p>}
      {!p ? <p className="muted">Carregando…</p> : (
        <>
          <dl className="dk-kv">
            <dt>Tipo de vendedor</dt><dd><strong>{p.person_type_label}</strong> <span className="faint">(perfil pretendido)</span></dd>
            <dt>Identificador fiscal</dt><dd>{p.tax_id_note}</dd>
            <dt>Análise jurídica/fiscal</dt><dd>{GATE_STATE[p.legal_review] ?? p.legal_review} <span className="faint">(homologação LEG-01)</span></dd>
            <dt>Modelo fiscal</dt><dd>{p.fiscal_model_label}</dd>
            <dt>Canais para venda real</dt><dd>{elig.map((e) => (
              <span key={e.channel} className={`dk-chip ${e.status ? ELIG[e.status].tone : ""}`} style={{ marginRight: 6 }}>
                {channelName(e.channel)}: {e.status ? ELIG[e.status].label : "via homologação do canal"}</span>))}</dd>
          </dl>
          <p className="muted" style={{ marginBottom: 6 }}>{p.note}</p>
          {pending.length > 0 && (
            <details><summary className="faint">Requisitos externos ainda pendentes ({pending.length})</summary>
              <ul className="faint" style={{ margin: "6px 0 0", paddingLeft: 18 }}>{pending.map((x) => <li key={x}>{x}</li>)}</ul></details>
          )}
          {owner && (
            <div className="row-actions" style={{ marginTop: 10 }}>
              <button type="button" className="btn" disabled={!desk.online}
                onClick={() => setSwitchTo(p.person_type === "PERSON" ? "COMPANY" : "PERSON")}>
                Mudar para {p.person_type === "PERSON" ? SELLER_SHORT.COMPANY : SELLER_SHORT.PERSON}</button>
            </div>
          )}
        </>
      )}
      {switchTo && (
        <ConfirmDialog title={`Mudar o perfil para ${SELLER_SHORT[switchTo]}?`} confirmLabel="Mudar perfil" danger
          body={<>As homologações aprovadas que dependem do perfil (LEG-01, fiscais e de canais) voltam a precisar de revisão. Nada é aprovado automaticamente.</>}
          onCancel={() => setSwitchTo(null)}
          onConfirm={async () => {
            const r = await api.updateSellerProfile(desk.token, { person_type: switchTo });
            setSwitchTo(null); load();
            desk.notify(r.gates_lost.length ? `Perfil alterado; precisam de nova revisão: ${r.gates_lost.join(", ")}` : "Perfil alterado no servidor.");
          }} />
      )}
    </section>
  );
}
