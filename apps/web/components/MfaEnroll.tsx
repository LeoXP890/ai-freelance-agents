"use client";
// Cadastro self-service do segundo fator: QR code + chave manual + confirmação com o código do app.
// O MFA só fica ativo no backend depois da confirmação.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type MfaEnrollment } from "@/lib/api";
import { groupSecret, normalizeCode } from "@/lib/totp";
import RecoveryCodes from "./RecoveryCodes";

export default function MfaEnroll({ enrollmentToken, onToken, onCancel }: {
  enrollmentToken: string; onToken(token: string): void; onCancel(): void;
}) {
  const [data, setData] = useState<MfaEnrollment | null>(null);
  const [code, setCode] = useState("");
  const [done, setDone] = useState<{ token: string; codes: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showKey, setShowKey] = useState(false);

  const start = useCallback(async () => {
    setError(null);
    setData(null);
    setCode("");
    try {
      setData(await api.enrollStart(enrollmentToken));
    } catch (e) {
      setError(e instanceof ApiError
        ? (e.status === 401 ? "Sessão de cadastro expirou. Entre novamente." : e.message)
        : "Falha de conexão com a API");
    }
  }, [enrollmentToken]);

  useEffect(() => { void start(); }, [start]);

  async function confirm(e: FormEvent) {
    e.preventDefault();
    if (code.length !== 6) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.enrollConfirm(enrollmentToken, code);
      setDone({ token: r.access_token, codes: r.recovery_codes });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha de conexão com a API");
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  if (done) return <RecoveryCodes codes={done.codes} onContinue={() => onToken(done.token)} />;

  async function copyKey() {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setShowKey(true);
    }
  }

  return (
    <form onSubmit={confirm} className="card enroll" aria-labelledby="enroll-title">
      <div className="muted small">SEGURANÇA DA CONTA</div>
      <h1 id="enroll-title">Configurar verificação em duas etapas</h1>
      <p className="muted small">Seu papel exige um segundo fator. É só uma vez: depois, cada login pede o
        código de 6 dígitos do app.</p>

      <ol className="steps">
        <li>Abra um app autenticador (Google Authenticator, Microsoft Authenticator, 1Password, Authy).</li>
        <li>Toque em <strong>+</strong> e escaneie o QR code abaixo.</li>
        <li>Digite o código de 6 dígitos que o app mostrar.</li>
      </ol>

      <div className="qr-box" aria-live="polite">
        {data ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={data.qr_svg_data_uri} width={208} height={208}
            alt={`QR code para cadastrar ${data.account} no app autenticador`} />
        ) : !error ? <div className="qr-placeholder">Gerando QR code…</div> : null}
      </div>

      {data && (
        <details open={showKey} onToggle={(e) => setShowKey((e.target as HTMLDetailsElement).open)}>
          <summary>Não consegue escanear? Digite a chave</summary>
          <div className="manual">
            <div className="muted small">Conta: {data.account} · Tipo: baseado em tempo</div>
            <code className="secret" aria-label="Chave de configuração">{groupSecret(data.secret)}</code>
            <button type="button" onClick={copyKey}>{copied ? "Copiada ✓" : "Copiar chave"}</button>
          </div>
        </details>
      )}

      <label>Código do app
        <input inputMode="numeric" autoComplete="one-time-code" placeholder="000000" required
          value={code} onChange={(e) => setCode(normalizeCode(e.target.value))} disabled={!data}
          aria-describedby="enroll-hint" className="code-input" />
      </label>
      <p id="enroll-hint" className="muted small">Se o código for recusado, confira se o horário do celular
        está automático e use o código atual.</p>
      {error && <p className="err" role="alert">{error}</p>}
      <button className="primary" type="submit" disabled={busy || !data || code.length !== 6}>
        {busy ? "Verificando…" : "Ativar e entrar"}
      </button>
      <div className="row">
        <button type="button" onClick={() => void start()} disabled={busy}>Gerar novo QR</button>
        <button type="button" onClick={onCancel} disabled={busy}>Voltar</button>
      </div>
    </form>
  );
}
