// Utilitários de apresentação do cadastro de MFA (sem lógica criptográfica: o backend valida tudo).

/** Agrupa a chave base32 em blocos de 4 para digitação manual no app autenticador. */
export function groupSecret(secret: string): string {
  return secret.replace(/\s+/g, "").match(/.{1,4}/g)?.join(" ") ?? "";
}

/** Mantém apenas dígitos e limita a 6 (aceita colar "123 456"). */
export function normalizeCode(raw: string): string {
  return raw.replace(/\D/g, "").slice(0, 6);
}
