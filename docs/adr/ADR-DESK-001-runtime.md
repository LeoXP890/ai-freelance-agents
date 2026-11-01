# ADR-DESK-001 — Runtime do aplicativo desktop

**Data:** 2026-10-24 · **Estado:** aceito (revisão humana pendente) · **Contexto:** SDD v3 §3, §10 (Fase 0/1) · UXD-002

## Decisão
**Tauri 2 + o frontend React/Next existente em exportação estática + API remota FastAPI.** Electron não foi
necessário: nenhum critério de exceção do SDD §3 foi reprovado nos testes abaixo.

## Evidências (medidas, não presumidas)

### 1. Compatibilidade do frontend atual com exportação estática
- Auditoria (`docs/desktop-migration/current-state-audit.md`): 17/17 componentes `"use client"`, uma rota, **nenhum**
  route handler, server action, middleware, `next/headers`/cookies ou SSR de dados.
- `AIC_TARGET=desktop next build` gera `out/` estático (rotas `/` e `/probe`) sem alterações de código além de
  `output: "export"` condicional em `next.config.mjs`. O alvo web (`standalone`, compose) continua igual.
- **Conclusão:** manter Next.js; **não** migrar para Vite (o SDD só exige Vite se houver dependência de SSR).

### 2. WebGL real no runtime-alvo (macOS / WKWebView)
Máquina: Apple M5 (Metal 4), macOS 26.6.2, tela 2560×1664 Retina.
| Teste | Resultado |
|---|---|
| Página de diagnóstico (three + R3F, 64 objetos animados, sombras) dentro do Tauri | WebGL2 ✔ · GPU "Apple GPU" · 53,4 FPS · 129 draw calls · DPR 2 (`evidence/webgl_probe_wkwebview.json`, quadro em `evidence/webgl_probe_frame.png`) |
| **App real** (Centro de comando → Escritório 3D) dentro do Tauri, autoteste | conectado à API, modo SIMULAÇÃO, 5 KPIs reais carregados; sala 3D WebGL2 na Apple GPU, 2 placas de nome, **58,4 FPS**, 221 draw calls, 101 mil triângulos (`evidence/desktop_selftest_*.json`) |

O autoteste (`apps/web/desktop/selftest.ts`) roda o app de verdade no WKWebView e reporta só para `127.0.0.1`.

### 3. Empacotamento
`npm run desktop:build` → `AI Commerce.app` **4,6 MB** e `AI Commerce_0.1.0_aarch64.dmg` **2,3 MB**; processo
principal com ~83 MB de memória residente após abrir. (Electron empacotaria um Chromium inteiro: ~100 MB+.)

## Consequências
- **Rust** passa a ser pré-requisito de build do desktop (instalado via rustup 1.99.0, sem alterar o PATH do shell;
  CI precisará de `dtolnay/rust-toolchain` ou equivalente).
- A UI fala com a API por HTTP(S)/WS(S): CORS da API inclui `tauri://localhost` (macOS/Linux) e
  `http://tauri.localhost` (Windows) — CORS não autoriza nada; JWT/RBAC continuam no backend.
- Endereço da API configurável em tempo de execução (Configurações → Servidor), sem recompilar.
- CSP restrita no `tauri.conf.json`; capacidades Tauri mínimas (`core:default`, sem shell/fs/clipboard/http nativo).
- Fallback: a mesma UI roda no navegador (dev) e a interface anterior segue em `?ui=legacy` (rollback).

## Pendências (não decididas aqui)
- **Windows/WebView2: NÃO testado** (sem máquina Windows). Precisa do mesmo autoteste antes de distribuir.
- Assinatura/notarização Apple (sem certificado Developer ID) e assinatura Windows; updater assinado.
- Token em `sessionStorage`; o SDD pede cofre do SO (UXD-004) — próximo passo: plugin de keychain do Tauri.
