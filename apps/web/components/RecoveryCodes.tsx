"use client";
// Códigos de recuperação do MFA: exibidos uma única vez (o backend guarda só o hash).
import { useState } from "react";

export default function RecoveryCodes({ codes, onContinue, continueLabel = "Continuar" }: {
  codes: string[]; onContinue(): void; continueLabel?: string;
}) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const text = `AI Commerce — códigos de recuperação do MFA\nCada código funciona uma única vez.\n\n${codes.join("\n")}\n`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "aicommerce-codigos-recuperacao.txt" });
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="card enroll" aria-labelledby="rc-title">
      <h1 id="rc-title">Guarde seus códigos de recuperação</h1>
      <p className="muted small">
        Se perder o celular, cada código abaixo substitui o código do app <strong>uma única vez</strong>.
        Eles não serão mostrados novamente.
      </p>
      <ol className="recovery-codes" aria-label="Códigos de recuperação">
        {codes.map((c) => <li key={c}><code>{c}</code></li>)}
      </ol>
      <div className="row">
        <button type="button" onClick={copy}>{copied ? "Copiados" : "Copiar"}</button>
        <button type="button" onClick={download}>Baixar .txt</button>
      </div>
      <label className="check">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        Guardei os códigos em local seguro
      </label>
      <button type="button" className="primary" disabled={!saved} onClick={onContinue}>{continueLabel}</button>
    </section>
  );
}
