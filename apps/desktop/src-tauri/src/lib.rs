//! Shell desktop do AI Commerce (ADR-DESK-001). Toda a lógica de negócio fica na API da VPS;
//! este binário só hospeda a UI (exportação estática do apps/web) numa janela nativa e guarda,
//! no cofre do sistema operacional, o segredo que permite abrir o app sem login neste computador.
//!
//! UXD-021: menu nativo (Preferências, navegação, Editar, Janela), ícone na bandeja com aprovações pendentes,
//! fechar a janela mantém o app na bandeja (configurável) e notificações do sistema. Os agentes rodam na VPS:
//! fechar ou minimizar o app nunca para o trabalho.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use tauri::menu::{AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WindowEvent, Wry};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_updater::UpdaterExt;

const SERVICE: &str = "br.com.aicommerce.desktop";

/// https em qualquer host; http só local (localhost/127.0.0.1) — credencial nunca associada a servidor remoto sem TLS.
fn server_allowed(server: &str) -> bool {
    if server.len() > 300 {
        return false;
    }
    if server.starts_with("https://") {
        return true;
    }
    let rest = match server.strip_prefix("http://") {
        Some(r) => r,
        None => return false,
    };
    let host = rest.split(['/', ':']).next().unwrap_or("");
    host == "localhost" || host == "127.0.0.1"
}

/// Link externo que pode ir para o navegador do sistema: só https, sem espaço/controle, tamanho limitado
/// (ex.: autorizar a conta do Mercado Livre, abrir a fonte de um documento).
fn external_url_allowed(url: &str) -> bool {
    url.len() <= 2048
        && url.starts_with("https://")
        && url.len() > "https://".len()
        && !url.chars().any(|c| c.is_whitespace() || c.is_control() || c == '"' || c == '\\')
}

fn entry(server: &str) -> Result<keyring::Entry, String> {
    if !server_allowed(server) {
        return Err("endereço de servidor inválido (use https; http só local)".into());
    }
    keyring::Entry::new(SERVICE, server).map_err(|e| e.to_string())
}

/// Keychain fora da thread principal: comando síncrono do Tauri roda na thread da interface, e o macOS pode
/// pedir permissão de acesso (a cada build com assinatura nova) — enquanto ninguém responde, a janela, o menu e o
/// "Sair" ficariam congelados. Aqui a espera acontece numa thread de fundo.
async fn off_main<T: Send + 'static>(f: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(f).await.map_err(|e| e.to_string())?
}

/// Lê o segredo do computador lembrado para este servidor (None se não houver).
#[tauri::command]
async fn device_token_get(server: String) -> Result<Option<String>, String> {
    off_main(move || match entry(&server)?.get_password() {
        Ok(t) => Ok(Some(t)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    })
    .await
}

#[tauri::command]
async fn device_token_set(server: String, token: String) -> Result<(), String> {
    if !token.starts_with("aicd_") || token.len() > 200 {
        return Err("segredo de dispositivo inválido".into());
    }
    off_main(move || entry(&server)?.set_password(&token).map_err(|e| e.to_string())).await
}

#[tauri::command]
async fn device_token_delete(server: String) -> Result<(), String> {
    off_main(move || match entry(&server)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    })
    .await
}

/// Estado nativo da janela: fechar = esconder na bandeja? (padrão sim) e item de aprovações da bandeja.
struct Shell {
    keep_in_tray: Mutex<bool>,
    pending_item: Mutex<Option<MenuItem<Wry>>>,
    /// Saída pedida (menu Sair, ⌘Q, bandeja → Sair): a partir daí, fechar a janela não vira "esconder na bandeja".
    quitting: AtomicBool,
}

/// Preferência "ao fechar a janela" (Configurações): "tray" mantém o app na bandeja, "quit" encerra.
#[tauri::command]
fn set_close_behavior(state: tauri::State<'_, Shell>, behavior: String) -> Result<(), String> {
    let keep = match behavior.as_str() {
        "tray" => true,
        "quit" => false,
        _ => return Err("comportamento inválido".into()),
    };
    *state.keep_in_tray.lock().map_err(|e| e.to_string())? = keep;
    Ok(())
}

/// Atualiza o item "Aprovações pendentes" da bandeja (o número vem da API, não é calculado aqui).
#[tauri::command]
fn set_tray_status(state: tauri::State<'_, Shell>, pending: u32) -> Result<(), String> {
    if let Some(item) = state.pending_item.lock().map_err(|e| e.to_string())?.as_ref() {
        let text = match pending {
            0 => "Nenhuma aprovação pendente".to_string(),
            1 => "1 aprovação pendente".to_string(),
            n => format!("{n} aprovações pendentes"),
        };
        item.set_text(text).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Notificação do sistema (texto curto; nunca dados pessoais ou valores sensíveis).
#[tauri::command]
fn notify(app: AppHandle, title: String, body: String) -> Result<(), String> {
    if title.len() > 120 || body.len() > 300 {
        return Err("notificação longa demais".into());
    }
    app.notification().builder().title(title).body(body).show().map_err(|e| e.to_string())
}

/// Abrir com o sistema (opt-in). Desligado por padrão; só o usuário liga, em Configurações.
/// Abre um link https no navegador padrão (sem shell: argumentos vão direto ao programa do sistema).
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    if !external_url_allowed(&url) {
        return Err("link externo recusado (só https)".into());
    }
    #[cfg(target_os = "macos")]
    let r = std::process::Command::new("open").arg(&url).spawn();
    #[cfg(target_os = "windows")]
    let r = std::process::Command::new("rundll32").args(["url.dll,FileProtocolHandler", url.as_str()]).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let r = std::process::Command::new("xdg-open").arg(&url).spawn();
    r.map(|_| ()).map_err(|e| e.to_string())
}

/// Endereço de atualização no servidor configurado (o mesmo da API). Em release o updater exige https.
fn update_endpoint(server: &str) -> Result<tauri::Url, String> {
    if !server_allowed(server) {
        return Err("endereço de servidor inválido (use https; http só local)".into());
    }
    let base = server.trim_end_matches('/');
    tauri::Url::parse(&format!("{base}/v1/desktop/update/{{{{target}}}}/{{{{arch}}}}/{{{{current_version}}}}"))
        .map_err(|e| e.to_string())
}

#[derive(serde::Serialize)]
struct UpdateInfo {
    version: String,
    current_version: String,
    notes: Option<String>,
}

fn updater(app: &AppHandle, server: &str) -> Result<tauri_plugin_updater::Updater, String> {
    app.updater_builder()
        .endpoints(vec![update_endpoint(server)?])
        .map_err(|e| e.to_string())?
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())
}

/// Há versão nova publicada no servidor? (None = já está na mais nova)
#[tauri::command]
async fn update_check(app: AppHandle, server: String) -> Result<Option<UpdateInfo>, String> {
    let found = updater(&app, &server)?.check().await.map_err(|e| e.to_string())?;
    Ok(found.map(|u| UpdateInfo { version: u.version, current_version: u.current_version, notes: u.body }))
}

/// Baixa, confere a assinatura (chave pública embutida no app) e instala; depois reinicia o app.
#[tauri::command]
async fn update_install(app: AppHandle, server: String) -> Result<(), String> {
    let Some(update) = updater(&app, &server)?.check().await.map_err(|e| e.to_string())? else {
        return Err("nenhuma atualização disponível".into());
    };
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    app.state::<Shell>().quitting.store(true, Ordering::SeqCst);
    app.restart()
}

#[tauri::command]
fn autostart_get(app: AppHandle) -> Result<bool, String> {
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
fn autostart_set(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let m = app.autolaunch();
    if enabled { m.enable() } else { m.disable() }.map_err(|e| e.to_string())?;
    m.is_enabled().map_err(|e| e.to_string())
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn quit(app: &AppHandle) {
    app.state::<Shell>().quitting.store(true, Ordering::SeqCst);
    app.exit(0);
}

/// Navegação pedida pelo menu/bandeja: a UI web escuta o evento "nav" e troca de página.
fn nav(app: &AppHandle, page: &str) {
    show_main(app);
    let _ = app.emit("nav", page);
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let about = AboutMetadata {
        name: Some("AI Commerce".into()),
        comments: Some("Centro de comando dos agentes. Os agentes trabalham no servidor (VPS).".into()),
        ..Default::default()
    };
    let app_menu = Submenu::with_items(
        app,
        "AI Commerce",
        true,
        &[
            &PredefinedMenuItem::about(app, Some("Sobre o AI Commerce"), Some(about))?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "nav:settings", "Preferências…", true, Some("CmdOrCtrl+,"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, Some("Ocultar AI Commerce"))?,
            &PredefinedMenuItem::hide_others(app, Some("Ocultar outros"))?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Sair do AI Commerce", true, Some("CmdOrCtrl+Q"))?,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        "Editar",
        true,
        &[
            &PredefinedMenuItem::undo(app, Some("Desfazer"))?,
            &PredefinedMenuItem::redo(app, Some("Refazer"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, Some("Recortar"))?,
            &PredefinedMenuItem::copy(app, Some("Copiar"))?,
            &PredefinedMenuItem::paste(app, Some("Colar"))?,
            &PredefinedMenuItem::select_all(app, Some("Selecionar tudo"))?,
        ],
    )?;
    // Atalhos ⌘1..5 já são tratados pela página (com a janela em foco); aqui ficam sem acelerador para não duplicar.
    let go = Submenu::with_items(
        app,
        "Ir",
        true,
        &[
            &MenuItem::with_id(app, "nav:command", "Centro de comando", true, None::<&str>)?,
            &MenuItem::with_id(app, "nav:office", "Escritório 3D", true, None::<&str>)?,
            &MenuItem::with_id(app, "nav:approvals", "Aprovações", true, None::<&str>)?,
            &MenuItem::with_id(app, "nav:agents", "Agentes", true, None::<&str>)?,
            &MenuItem::with_id(app, "nav:chat", "Conversas", true, None::<&str>)?,
            &MenuItem::with_id(app, "nav:missions", "Missões & tarefas", true, None::<&str>)?,
            &MenuItem::with_id(app, "nav:finance", "Financeiro", true, None::<&str>)?,
        ],
    )?;
    let window = Submenu::with_items(
        app,
        "Janela",
        true,
        &[
            &PredefinedMenuItem::minimize(app, Some("Minimizar"))?,
            &PredefinedMenuItem::maximize(app, Some("Zoom"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, Some("Fechar janela"))?,
        ],
    )?;
    Menu::with_items(app, &[&app_menu, &edit, &go, &window])
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, None))
        .manage(Shell { keep_in_tray: Mutex::new(true), pending_item: Mutex::new(None), quitting: AtomicBool::new(false) })
        .setup(|app| {
            let handle = app.handle().clone();
            app.set_menu(build_menu(&handle)?)?;
            app.on_menu_event(|app, e| match e.id().as_ref() {
                "quit" => quit(app),
                id if id.starts_with("nav:") => nav(app, &id[4..]),
                _ => {}
            });
            let pending = MenuItem::with_id(app, "tray:approvals", "Aprovações pendentes: —", true, None::<&str>)?;
            let tray_menu = Menu::with_items(
                app,
                &[
                    &MenuItem::with_id(app, "tray:open", "Abrir AI Commerce", true, None::<&str>)?,
                    &pending,
                    &PredefinedMenuItem::separator(app)?,
                    &MenuItem::with_id(app, "tray:quit", "Sair", true, None::<&str>)?,
                ],
            )?;
            *app.state::<Shell>().pending_item.lock().expect("estado da bandeja") = Some(pending);
            let mut tray = TrayIconBuilder::with_id("main")
                .tooltip("AI Commerce — agentes trabalhando no servidor")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, e| match e.id().as_ref() {
                    "tray:open" => show_main(app),
                    "tray:approvals" => nav(app, "approvals"),
                    "tray:quit" => quit(app),
                    _ => {}
                })
                .on_tray_icon_event(|tray, e| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = e {
                        show_main(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // fechar a janela mantém o app na bandeja (preferência); os agentes seguem na VPS de qualquer forma
            if let WindowEvent::CloseRequested { api, .. } = event {
                let shell = window.state::<Shell>();
                let keep = *shell.keep_in_tray.lock().expect("preferência de fechamento");
                if keep && !shell.quitting.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            device_token_get,
            device_token_set,
            device_token_delete,
            set_close_behavior,
            set_tray_status,
            notify,
            open_external,
            update_check,
            update_install,
            autostart_get,
            autostart_set
        ])
        .build(tauri::generate_context!())
        .expect("falha ao iniciar o AI Commerce Desktop")
        .run(|app, event| match event {
            // pedido de saída em andamento: janelas fecham de verdade (não voltam para a bandeja)
            tauri::RunEvent::ExitRequested { .. } => app.state::<Shell>().quitting.store(true, Ordering::SeqCst),
            // macOS: clicar no ícone do Dock com a janela escondida reabre a janela
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { .. } => show_main(app),
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::{external_url_allowed, server_allowed, update_endpoint};

    #[test]
    fn update_endpoint_uses_the_configured_server_and_tauri_placeholders() {
        let u = update_endpoint("https://api.minhaloja.com.br/api/").unwrap().to_string();
        assert!(u.starts_with("https://api.minhaloja.com.br/api/v1/desktop/update/"));
        assert!(u.contains("%7B%7Btarget%7D%7D/%7B%7Barch%7D%7D/%7B%7Bcurrent_version%7D%7D"));
        assert!(update_endpoint("http://api.minhaloja.com.br/api").is_err());
    }

    #[test]
    fn external_links_only_https_without_injection() {
        assert!(external_url_allowed("https://auth.mercadolivre.com.br/authorization?client_id=1&state=x"));
        assert!(!external_url_allowed("http://auth.mercadolivre.com.br/"));
        assert!(!external_url_allowed("file:///etc/passwd"));
        assert!(!external_url_allowed("https://"));
        assert!(!external_url_allowed("https://x.com/a b"));
        assert!(!external_url_allowed("https://x.com/\"\n--flag"));
        assert!(!external_url_allowed(&format!("https://x.com/{}", "a".repeat(2100))));
    }

    #[test]
    fn only_https_or_local_http() {
        assert!(server_allowed("https://api.minhaloja.com.br/api"));
        assert!(server_allowed("http://localhost:8080/api"));
        assert!(server_allowed("http://127.0.0.1:8080"));
        assert!(!server_allowed("http://api.minhaloja.com.br/api"));
        assert!(!server_allowed("http://localhost.evil.com/api"));
        assert!(!server_allowed("ftp://localhost"));
    }
}
