// "Computador lembrado" (app desktop): depois do primeiro login, o app abre direto. O segredo do dispositivo fica
// no cofre do sistema operacional (Keychain/Credential Manager) via comandos Rust do Tauri — nunca em localStorage.
// No navegador (dev) nada disso existe: segue o login normal.
import { invoke, isTauri } from "@tauri-apps/api/core";
import { apiUrl, ApiError } from "@/lib/api";

export const RENEW_MS = 12 * 60 * 1000; // token de acesso dura 15 min

export function isDesktop(): boolean {
  try { return isTauri(); } catch { return false; }
}

async function post<T>(path: string, body: unknown, token?: string): Promise<T> {
  const res = await fetch(`${apiUrl()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : res.statusText);
  return data as T;
}

/** Autoteste de renderização (build de teste): não toca no cofre do sistema — evita pedido de acesso ao Keychain
 *  de uma credencial gravada pelo app instalado (assinatura diferente). O modo "device" testa o cofre de propósito. */
function renderSelfTest(): boolean {
  if (process.env.NEXT_PUBLIC_AIC_SELFTEST !== "1") return false;
  try {
    const q = new URLSearchParams(window.location.search);
    const on = q.has("selftest") || !!sessionStorage.getItem("aic.selftest");
    const mode = q.get("mode") ?? sessionStorage.getItem("aic.selftest.mode");
    return on && mode !== "device";
  } catch { return false; }
}

/** Tenta entrar com o segredo guardado. null = não há computador lembrado (ou foi revogado). */
export async function signInWithDevice(): Promise<string | null> {
  if (!isDesktop() || renderSelfTest()) return null;
  const server = apiUrl();
  const secret = await invoke<string | null>("device_token_get", { server }).catch(() => null);
  if (!secret) return null;
  try {
    return (await post<{ access_token: string }>("/v1/auth/device-login", { device_token: secret })).access_token;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) await forgetDevice(); // revogado/senha trocada: pede login 1 vez
    return null;
  }
}

/** Depois do login manual no desktop: lembra este computador (não pede mais login). */
export async function rememberDevice(accessToken: string): Promise<void> {
  if (!isDesktop()) return;
  const name = `${navigator.platform.includes("Mac") ? "Mac" : navigator.platform.includes("Win") ? "Windows" : "Computador"} · app desktop`;
  const r = await post<{ device_token: string }>("/v1/auth/devices", { name }, accessToken);
  await invoke("device_token_set", { server: apiUrl(), token: r.device_token });
}

export async function forgetDevice(): Promise<void> {
  if (!isDesktop()) return;
  await invoke("device_token_delete", { server: apiUrl() }).catch(() => undefined);
}

export async function hasRememberedDevice(): Promise<boolean> {
  if (!isDesktop()) return false;
  return !!(await invoke<string | null>("device_token_get", { server: apiUrl() }).catch(() => null));
}
