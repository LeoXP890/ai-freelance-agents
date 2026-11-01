"use client";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { normalizeCode } from "@/lib/totp";
import MfaEnroll from "./MfaEnroll";

type Step = { kind: "password" } | { kind: "verify"; challenge: string } | { kind: "enroll"; token: string };

export default function Login({ onToken }: { onToken(token: string): void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [useRecovery, setUseRecovery] = useState(false);

  const reset = () => {
    setStep({ kind: "password" }); setCode(""); setPassword(""); setError(null); setUseRecovery(false);
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (step.kind === "password") {
        const r = await api.login(email, password);
        if (!r.mfa_required) onToken(r.access_token);
        else if (r.mfa_enrollment_required) setStep({ kind: "enroll", token: r.enrollment_token });
        else setStep({ kind: "verify", challenge: r.challenge_token });
      } else if (step.kind === "verify") {
        onToken(useRecovery
          ? (await api.recoverMfa(step.challenge, code)).access_token
          : (await api.verifyMfa(step.challenge, code)).access_token);
      }
    } catch (err) {
      if (err instanceof ApiError && step.kind === "verify" && err.status === 401 && /sessão/.test(err.message)) {
        reset();
        setError("O tempo para digitar o código expirou. Entre novamente.");
      } else {
        setError(err instanceof ApiError ? err.message : "Falha de conexão com a API");
        setCode("");
      }
    } finally {
      setBusy(false);
    }
  }

  if (step.kind === "enroll") {
    return (
      <main className="login">
        <MfaEnroll enrollmentToken={step.token} onToken={onToken} onCancel={reset} />
      </main>
    );
  }

  return (
    <main className="login">
      <form onSubmit={submit} className="card" aria-labelledby="login-title">
        <div className="muted small">AI COMMERCE · ESCRITÓRIO</div>
        <h1 id="login-title">{step.kind === "verify" ? "Verificação em duas etapas" : "Entrar"}</h1>
        {step.kind === "password" ? (
          <>
            <label>E-mail<input type="email" autoComplete="username" required value={email}
              onChange={(e) => setEmail(e.target.value)} /></label>
            <label>Senha<input type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} /></label>
          </>
        ) : useRecovery ? (
          <label>Código de recuperação
            <input autoComplete="off" placeholder="XXXX-XXXX-XXXX-XXXX" required autoFocus spellCheck={false}
              className="code-input" value={code} onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, 24))} />
          </label>
        ) : (
          <label>Código de 6 dígitos do app autenticador
            <input inputMode="numeric" autoComplete="one-time-code" placeholder="000000" required autoFocus
              className="code-input" value={code} onChange={(e) => setCode(normalizeCode(e.target.value))} />
          </label>
        )}
        {error && <p className="err" role="alert">{error}</p>}
        <button className="primary" type="submit"
          disabled={busy || (step.kind === "verify" && (useRecovery ? code.replace(/[^A-Z0-9]/g, "").length !== 16 : code.length !== 6))}>
          {step.kind === "verify" ? "Verificar" : "Entrar"}
        </button>
        {step.kind === "verify"
          ? <>
              <button type="button" className="link" onClick={() => { setUseRecovery(!useRecovery); setCode(""); setError(null); }}>
                {useRecovery ? "Usar o app autenticador" : "Perdi o celular — usar código de recuperação"}
              </button>
              <button type="button" onClick={reset}>Voltar</button>
            </>
          : <p className="muted small">Papéis administrativos exigem verificação em duas etapas (TOTP).</p>}
      </form>
    </main>
  );
}
