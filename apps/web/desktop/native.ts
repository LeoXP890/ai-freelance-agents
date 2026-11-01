// Integração com o shell nativo (UXD-021): menu e bandeja do sistema, notificações e comportamento ao fechar.
// No navegador (dev/legado) tudo vira no-op. Preferências deste computador ficam no localStorage — nunca políticas.
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { isDesktop } from "./device";

export const CLOSE_KEY = "aic.closeBehavior";   // "tray" (padrão) | "quit"
export const NOTIFY_KEY = "aic.notifications";  // "on" (padrão) | "off"
const get = (k: string, d: string) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };

export const closeBehavior = () => get(CLOSE_KEY, "tray");
export const notificationsOn = () => get(NOTIFY_KEY, "on") === "on";

export async function applyCloseBehavior(b = closeBehavior()): Promise<void> {
  if (isDesktop()) await invoke("set_close_behavior", { behavior: b }).catch(() => undefined);
}
export async function setTrayStatus(pending: number): Promise<void> {
  if (isDesktop()) await invoke("set_tray_status", { pending: Math.max(0, Math.floor(pending)) }).catch(() => undefined);
}
/** Notifica só com a janela fora de foco (com ela em foco a própria tela mostra) e se o usuário não desligou. */
export async function notifyOS(title: string, body: string): Promise<boolean> {
  if (!isDesktop() || !notificationsOn() || (document.hasFocus() && !document.hidden)) return false;
  await invoke("notify", { title: title.slice(0, 120), body: body.slice(0, 300) }).catch(() => undefined);
  return true;
}
export function onNativeNav(cb: (page: string) => void): () => void {
  if (!isDesktop()) return () => undefined;
  let off: (() => void) | null = null;
  let cancelled = false;
  void listen<string>("nav", (e) => cb(e.payload)).then((u) => { if (cancelled) u(); else off = u; });
  return () => { cancelled = true; off?.(); };
}
export async function appVersion(): Promise<string | null> {
  if (!isDesktop()) return null;
  try { const { getVersion } = await import("@tauri-apps/api/app"); return await getVersion(); } catch { return null; }
}
/** Link https no navegador padrão (app: comando nativo que só aceita https; navegador: nova aba sem opener). */
export async function openExternal(url: string): Promise<boolean> {
  if (!/^https:\/\/\S+$/.test(url)) return false;
  if (!isDesktop()) { window.open(url, "_blank", "noopener,noreferrer"); return true; }
  try { await invoke("open_external", { url }); return true; } catch { return false; }
}
/** Abrir com o sistema (opt-in). null = indisponível (navegador) ou erro. */
export async function autostartGet(): Promise<boolean | null> {
  if (!isDesktop()) return null;
  try { return await invoke<boolean>("autostart_get"); } catch { return null; }
}
export async function autostartSet(enabled: boolean): Promise<boolean | null> {
  if (!isDesktop()) return null;
  try { return await invoke<boolean>("autostart_set", { enabled }); } catch { return null; }
}

export interface UpdateInfo { version: string; current_version: string; notes: string | null }
/** Versão nova publicada no servidor configurado? null = já está na mais nova; lança erro se não deu para consultar. */
export async function updateCheck(server: string): Promise<UpdateInfo | null> {
  if (!isDesktop()) return null;
  return invoke<UpdateInfo | null>("update_check", { server });
}
/** Baixa, confere a assinatura com a chave embutida no app, instala e reinicia. */
export async function updateInstall(server: string): Promise<void> {
  if (isDesktop()) await invoke("update_install", { server });
}
