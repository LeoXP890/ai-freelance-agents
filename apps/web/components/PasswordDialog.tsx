"use client";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";

export default function PasswordDialog({ token, onChanged, onClose }: {
  token: string; onChanged(newToken: string): void; onClose(): void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== confirm) {
      setError("A confirmação não confere com a nova senha.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.changePassword(token, current, next);
      setDone(true);
      onChanged(r.access_token);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha de conexão com a API");
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="panel" aria-label="Trocar senha">
      <header className="panel-head">
        <h2>Trocar senha</h2>
        <button type="button" onClick={onClose} aria-label="Fechar">✕</button>
      </header>
      {done ? (
        <div className="block">
          <p className="good">Senha alterada. Outras sessões abertas foram encerradas.</p>
          <button type="button" onClick={onClose}>Fechar</button>
        </div>
      ) : (
        <form onSubmit={submit} className="stack">
          <label>Senha atual<input type="password" autoComplete="current-password" required value={current}
            onChange={(e) => setCurrent(e.target.value)} /></label>
          <label>Nova senha (mín. 12 caracteres)<input type="password" autoComplete="new-password" required
            minLength={12} value={next} onChange={(e) => setNext(e.target.value)} /></label>
          <label>Confirmar nova senha<input type="password" autoComplete="new-password" required minLength={12}
            value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
          {error && <p className="err" role="alert">{error}</p>}
          <button className="primary" type="submit" disabled={busy}>{busy ? "Salvando…" : "Salvar nova senha"}</button>
          <p className="muted small">Ao trocar, todas as outras sessões abertas são encerradas.</p>
        </form>
      )}
    </aside>
  );
}
