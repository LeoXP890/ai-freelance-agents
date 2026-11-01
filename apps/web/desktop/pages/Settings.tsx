"use client";
// UX-10 Configurações (UXD-005/021): servidor (VPS), qualidade gráfica, interface anterior (rollback), conta,
// computadores lembrados e usuários. Separa o que é deste computador das políticas remotas de dinheiro e contas.
import { Bell, Monitor, Server } from "lucide-react";
import { useEffect, useState } from "react";
import { api, apiUrl, SERVER_KEY, serverUrlAllowed } from "@/lib/api";
import PasswordDialog from "@/components/PasswordDialog";
import UsersPanel from "@/components/UsersPanel";
import { useDesk } from "../context";
import { Skeleton } from "../table";
import { isDesktop } from "../device";
import { appVersion, applyCloseBehavior, autostartGet, autostartSet, CLOSE_KEY, closeBehavior, NOTIFY_KEY, notificationsOn, type UpdateInfo, updateCheck, updateInstall } from "../native";
import { QUALITY_KEY } from "./Office";
import SellerProfileCard from "../SellerProfileCard";

const TRANSPORT_LABEL: Record<string, string> = { websocket: "tempo real (WebSocket)", polling: "consulta periódica (HTTP)", connecting: "conectando", offline: "sem conexão" };

function ThisComputer() {
  const desk = useDesk();
  const [close, setClose] = useState("tray");
  const [notify, setNotify] = useState(true);
  const [version, setVersion] = useState<string | null>(null);
  const [autostart, setAutostart] = useState<boolean | null>(null);
  // Atualização automática: o servidor (o mesmo da API) publica versões; o app só instala pacote assinado pela
  // chave do proprietário. Nada é baixado sem o usuário pedir.
  const [upd, setUpd] = useState<{ state: "idle" | "checking" | "none" | "available" | "installing" | "error"; info?: UpdateInfo; error?: string }>({ state: "idle" });
  const check = async () => {
    setUpd({ state: "checking" });
    try { const info = await updateCheck(apiUrl()); setUpd(info ? { state: "available", info } : { state: "none" }); }
    catch (e) { setUpd({ state: "error", error: e instanceof Error ? e.message : String(e) }); }
  };
  const install = async () => {
    setUpd((u) => ({ ...u, state: "installing" }));
    try { await updateInstall(apiUrl()); } // reinicia o app ao terminar
    catch (e) { setUpd({ state: "error", error: e instanceof Error ? e.message : String(e) }); }
  };
  useEffect(() => { setClose(closeBehavior()); setNotify(notificationsOn()); void appVersion().then(setVersion); void autostartGet().then(setAutostart); }, []);
  const save = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* */ } };
  return (
    <section className="dk-card" aria-label="Este computador">
      <div className="card-title"><Monitor size={16} />Este computador</div>
      <label style={{ display: "grid", gap: 6, marginBottom: 12 }}><span className="faint">Tema</span>
        <select className="field" value={desk.themePref}
          onChange={(e) => desk.setThemePref(e.target.value as "system" | "dark" | "light")}>
          <option value="system">Igual ao sistema ({desk.theme === "dark" ? "escuro" : "claro"} agora)</option>
          <option value="dark">Escuro</option>
          <option value="light">Claro</option>
        </select></label>
      {isDesktop() ? (
        <label style={{ display: "grid", gap: 6, marginBottom: 12 }}><span className="faint">Ao fechar a janela</span>
          <select className="field" value={close} onChange={(e) => { setClose(e.target.value); save(CLOSE_KEY, e.target.value); void applyCloseBehavior(e.target.value); }}>
            <option value="tray">Manter o app na bandeja (avisa sobre aprovações novas)</option>
            <option value="quit">Encerrar o app</option>
          </select>
          <span className="faint">Em qualquer opção os agentes continuam trabalhando no servidor. ⌘Q sempre encerra.</span></label>
      ) : <p className="faint" style={{ marginTop: 0 }}>Bandeja, menu nativo e notificações existem só no app desktop.</p>}
      <label className="inline-field" style={{ marginBottom: 12 }}><input type="checkbox" checked={notify} disabled={!isDesktop()}
        onChange={(e) => { setNotify(e.target.checked); save(NOTIFY_KEY, e.target.checked ? "on" : "off"); }} />
        <Bell size={14} aria-hidden="true" /><span>Notificações do sistema (aprovação nova, kill switch) quando o app está em segundo plano</span></label>
      <label className="inline-field" style={{ marginBottom: 12 }}><input type="checkbox" checked={!!autostart} disabled={autostart === null}
        onChange={async (e) => { const r = await autostartSet(e.target.checked); setAutostart(r); desk.notify(r ? "O app abrirá ao ligar o computador." : "O app não abrirá sozinho."); }} />
        <span>Abrir o AI Commerce ao ligar o computador{autostart === null ? " (só no app desktop)" : ""}</span></label>
      <dl className="dk-kv">
        <dt>Versão do app</dt><dd>{version ?? (isDesktop() ? "—" : "navegador")}</dd>
        <dt>Atualizações</dt><dd>{!isDesktop() ? "só no app desktop" : (
          <span className="upd" role="status">
            {upd.state === "idle" && "o servidor publica as versões novas"}
            {upd.state === "checking" && "consultando o servidor…"}
            {upd.state === "none" && "você já está na versão mais nova"}
            {upd.state === "available" && upd.info && <>versão {upd.info.version} disponível{upd.info.notes ? ` — ${upd.info.notes}` : ""}</>}
            {upd.state === "installing" && "baixando e conferindo a assinatura… o app reinicia ao terminar"}
            {upd.state === "error" && `não foi possível atualizar: ${upd.error}`}
            {" "}{upd.state === "available"
              ? <button type="button" className="btn primary" disabled={!desk.online} onClick={() => void install()}>Instalar e reiniciar</button>
              : upd.state !== "checking" && upd.state !== "installing" && <button type="button" className="dk-link" disabled={!desk.online} onClick={() => void check()}>Verificar atualizações</button>}
          </span>)}</dd>
        <dt>Conexão</dt><dd>{TRANSPORT_LABEL[desk.transport] ?? desk.transport}{desk.syncedAt ? ` · último contato ${new Date(desk.syncedAt).toLocaleTimeString("pt-BR")}` : ""}</dd>
        <dt>Modo do servidor</dt><dd>{desk.office.simulated ? "SIMULAÇÃO (DRY_RUN)" : "PRODUÇÃO"}</dd>
        <dt>Idioma</dt><dd>Português (Brasil)</dd>
      </dl>
    </section>
  );
}

function Devices() {
  const desk = useDesk();
  const [items, setItems] = useState<{ id: string; name: string; created_at: string; last_used_at: string | null; revoked: boolean }[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // falha da API não pode parecer "nenhum computador" (induziria a achar que o acesso foi revogado; auditoria A21)
  const load = () => api.devices(desk.token).then((r) => { setItems(r.items); setLoadError(null); })
    .catch((e) => { setItems(null); setLoadError(e instanceof Error ? e.message : String(e)); });
  useEffect(() => { void load(); }, [desk.token]); // eslint-disable-line react-hooks/exhaustive-deps
  const active = items?.filter((d) => !d.revoked) ?? [];
  return (
    <section className="dk-card">
      <div className="card-title">Computadores lembrados</div>
      <p className="muted" style={{ marginTop: 0 }}>Nestes computadores o app abre sem pedir login. A credencial fica no cofre do sistema
        (Keychain) e deixa de valer se você trocar a senha ou redefinir o MFA.</p>
      {loadError ? <p role="alert" className="dk-error">Não foi possível consultar os computadores lembrados: {loadError}. <button type="button" className="dk-link" onClick={() => void load()}>Tentar de novo</button></p>
        : items === null ? <Skeleton rows={2} cols={2} /> : active.length === 0 ? <p className="muted">Nenhum computador lembrado.</p> : (
        <div className="rows">{active.map((d) => (
          <div key={d.id} className="row-item">
            <div className="grow"><div className="t">{d.name}</div>
              <div className="meta">lembrado em {new Date(d.created_at).toLocaleDateString("pt-BR")}{d.last_used_at ? ` · último uso ${new Date(d.last_used_at).toLocaleString("pt-BR")}` : ""}</div></div>
            <button type="button" className="btn danger" onClick={() => api.revokeDevice(desk.token, d.id).then(load)}>Revogar</button>
          </div>))}</div>
      )}
    </section>
  );
}

export default function Settings() {
  const desk = useDesk();
  const [server, setServer] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [quality, setQuality] = useState("auto");
  const [pwd, setPwd] = useState(false);
  useEffect(() => {
    setServer(apiUrl());
    try { setQuality(localStorage.getItem(QUALITY_KEY) ?? "auto"); } catch { /* */ }
    api.systemInfo().then((i) => setInfo(`API ${i.version} · ${i.environment} · ${i.mode}`)).catch(() => setInfo("API inacessível"));
  }, []);
  function saveServer() {
    if (!serverUrlAllowed(server)) { setSaved("Use https:// (http só é aceito para localhost neste computador)."); return; }
    try { localStorage.setItem(SERVER_KEY, server.replace(/\/$/, "")); } catch { /* */ }
    setSaved("Servidor salvo. Entre novamente para conectar.");
    desk.logout();
  }
  return (
    <div className="page">
    <div className="page-head"><div><h1>Configurações</h1><p>Preferências deste computador e da sua conta. Políticas de dinheiro e de contas ficam no servidor.</p></div></div>
    <div className="grid-2">
      <div className="dk-stack">
        <section className="dk-card">
          <div className="card-title"><Server size={16} />Servidor (VPS)</div>
          <p className="muted" style={{ marginTop: 0 }}>Os agentes rodam no servidor; este app só acompanha e pede ações autorizadas. Fechar o app não para o trabalho.</p>
          <label style={{ display: "grid", gap: 6 }}><span className="faint">Endereço da API</span>
            <input className="field" value={server} onChange={(e) => setServer(e.target.value)} spellCheck={false} /></label>
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
            <button type="button" className="btn" onClick={saveServer}>Salvar e reconectar</button>
            <span className="faint">{info}</span>
          </div>
          {saved && <p className="faint" role="status">{saved}</p>}
        </section>
        <ThisComputer />
        <section className="dk-card">
          <div className="card-title">Escritório 3D</div>
          <label style={{ display: "grid", gap: 6 }}><span className="faint">Qualidade gráfica</span>
            <select className="field" value={quality} onChange={(e) => { setQuality(e.target.value); try { localStorage.setItem(QUALITY_KEY, e.target.value); } catch { /* */ } }}>
              <option value="auto">Automática (começa alta e reduz sozinha se o computador não acompanhar)</option>
              <option value="high">Alta (sombras, resolução da tela)</option>
              <option value="balanced">Equilibrada (sombras leves, resolução padrão)</option>
              <option value="low">Baixa (sem sombras, para computadores mais simples)</option>
            </select></label>
          <p className="faint">Movimento reduzido segue a preferência do sistema operacional.</p>
        </section>
        <section className="dk-card">
          <div className="card-title">Interface anterior</div>
          <p className="muted" style={{ marginTop: 0 }}>Disponível durante a migração, com todas as funções antigas.</p>
          <a className="btn" href="?ui=legacy">Abrir interface anterior</a>
        </section>
      </div>
      <div className="dk-stack">
        <SellerProfileCard />
        <section className="dk-card legacy">
          <div className="card-title">Conta</div>
          <div className="muted">{desk.me?.email} · {desk.me?.roles.join(", ")}</div>
          {pwd ? <PasswordDialog token={desk.token} onChanged={desk.replaceToken} onClose={() => setPwd(false)} />
            : <button type="button" className="btn" style={{ marginTop: 10 }} onClick={() => setPwd(true)}>Trocar senha</button>}
        </section>
        <Devices />
        {desk.can("users:manage") && (
          <section className="dk-card legacy"><UsersPanel token={desk.token} myEmail={desk.me?.email ?? ""} onClose={() => desk.go("command")} /></section>
        )}
      </div>
    </div>
    </div>
  );
}
