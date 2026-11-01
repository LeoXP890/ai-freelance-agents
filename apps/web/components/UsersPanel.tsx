"use client";
// Gestão de usuários (INF-003). O backend impede conceder permissões que o próprio ator não tem.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type UserRow } from "@/lib/api";
import { normalizeCode } from "@/lib/totp";
import RecoveryCodes from "./RecoveryCodes";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Falha de conexão com a API");

function MyRecovery({ token }: { token: string }) {
  const [left, setLeft] = useState<number | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.recoveryStatus(token).then((r) => setLeft(r.mfa_enabled ? r.remaining : null)).catch(() => undefined); },
    [token, codes]);
  if (codes) return <RecoveryCodes codes={codes} onContinue={() => setCodes(null)} continueLabel="Concluir" />;
  if (left === null) return null;
  async function regen(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try { setCodes((await api.regenerateRecovery(token, code)).recovery_codes); setCode(""); } catch (err) { setError(msg(err)); }
  }
  return (
    <form className="subcard" onSubmit={regen} aria-label="Meus códigos de recuperação">
      <h3>Meus códigos de recuperação</h3>
      <p className={left <= 3 ? "warn small" : "muted small"}>{left} de 10 disponíveis.</p>
      <label>Código atual do app para gerar novos (os antigos deixam de valer)
        <input inputMode="numeric" autoComplete="one-time-code" placeholder="000000" value={code}
          onChange={(e) => setCode(normalizeCode(e.target.value))} />
      </label>
      {error && <p className="err" role="alert">{error}</p>}
      <button type="submit" disabled={code.length !== 6}>Gerar novos códigos</button>
    </form>
  );
}

export default function UsersPanel({ token, myEmail, onClose }: { token: string; myEmail: string; onClose(): void }) {
  const [rows, setRows] = useState<UserRow[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);
  const [form, setForm] = useState({ email: "", name: "", role: "viewer" });

  const load = useCallback(async () => {
    try { const r = await api.users(token); setRows(r.items); setRoles(r.roles); } catch (e) { setError(msg(e)); }
  }, [token]);
  useEffect(() => { void load(); }, [load]);

  async function run(p: Promise<unknown>) {
    setError(null);
    try { await p; await load(); } catch (e) { setError(msg(e)); }
  }
  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await api.createUser(token, form.email, form.name, [form.role]);
      setCreated({ email: r.email, password: r.temporary_password });
      setForm({ email: "", name: "", role: "viewer" });
      await load();
    } catch (err) { setError(msg(err)); }
  }

  return (
    <aside className="panel" aria-label="Usuários">
      <header className="panel-head">
        <h2>Usuários</h2>
        <button type="button" onClick={onClose} aria-label="Fechar usuários">✕</button>
      </header>
      {error && <p className="err" role="alert">{error}</p>}
      {created && (
        <div className="subcard" role="status">
          <strong>Usuário {created.email} criado.</strong>
          <p className="small">Senha temporária (mostrada só agora; peça para trocar no primeiro acesso):</p>
          <code className="secret">{created.password}</code>
          <button type="button" onClick={() => setCreated(null)}>Ok</button>
        </div>
      )}
      <ul className="user-list">
        {rows.map((u) => {
          const self = u.email === myEmail;
          return (
            <li key={u.id} className={u.active ? "" : "inactive"}>
              <div>
                <strong>{u.display_name}</strong> <span className="muted small">{u.email}</span>
                <div className="small">
                  {u.active ? "Ativo" : "Desativado"} · MFA {u.mfa_enabled ? "ativo" : u.mfa_required ? "pendente" : "opcional"}
                  {u.locked && " · bloqueado"}{self && " · você"}
                </div>
              </div>
              <label className="small">Papel{" "}
                <select value={u.roles[0] ?? ""} disabled={self || u.roles.length > 1}
                  title={u.roles.length > 1 ? `Vários papéis: ${u.roles.join(", ")} (altere pela CLI)` : undefined}
                  onChange={(e) => run(api.setUserRoles(token, u.id, [e.target.value]))}>
                  {Object.keys(roles).map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </label>
              {!self && (
                <div className="row">
                  <button type="button" onClick={() => run(api.userAction(token, u.id, u.active ? "deactivate" : "activate"))}>
                    {u.active ? "Desativar" : "Reativar"}
                  </button>
                  {u.locked && <button type="button" onClick={() => run(api.userAction(token, u.id, "unlock"))}>Desbloquear</button>}
                  {u.mfa_enabled && (
                    <button type="button" onClick={() => run(api.userAction(token, u.id, "reset-mfa"))}>Redefinir MFA</button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <form className="subcard" onSubmit={create} aria-label="Novo usuário">
        <h3>Novo usuário</h3>
        <label>E-mail<input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
        <label>Nome<input required minLength={2} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Papel
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {Object.entries(roles).map(([r, d]) => <option key={r} value={r}>{r} — {d}</option>)}
          </select>
        </label>
        <button type="submit" className="primary">Criar</button>
      </form>
      <MyRecovery token={token} />
    </aside>
  );
}
