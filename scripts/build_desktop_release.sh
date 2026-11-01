#!/usr/bin/env bash
# Gera um release do app desktop com pacote de ATUALIZAÇÃO assinado (UXD-021) e mostra como publicá-lo no servidor.
# Uso (no Mac, na pasta do projeto): ./scripts/build_desktop_release.sh
#   1. aumente "version" em apps/desktop/src-tauri/tauri.conf.json (e Cargo.toml) antes — versões são imutáveis;
#   2. a chave do updater fica em infra/env/secrets/tauri_updater.key (+ .password), fora do git. GUARDE uma cópia:
#      sem ela, apps instalados não aceitam atualizações novas (seria preciso reinstalar à mão).
set -euo pipefail
cd "$(dirname "$0")/.."
KEY=infra/env/secrets/tauri_updater.key
PW=infra/env/secrets/tauri_updater.password
[ -f "$KEY" ] && [ -f "$PW" ] || { echo "falta $KEY / $PW" >&2; exit 1; }
VERSION="$(python3 -c "import json;print(json.load(open('apps/desktop/src-tauri/tauri.conf.json'))['version'])")"
ARCH="$(uname -m | sed 's/arm64/aarch64/')"
export TAURI_SIGNING_PRIVATE_KEY="$(cat "$KEY")" TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat "$PW")"
unset NEXT_PUBLIC_AIC_SELFTEST  # release nunca leva o autoteste
(cd apps/desktop && npx tauri build --config src-tauri/tauri.release.conf.json)
BUNDLE=apps/desktop/src-tauri/target/release/bundle/macos
PKG="$BUNDLE/AI Commerce.app.tar.gz"
[ -f "$PKG" ] && [ -f "$PKG.sig" ] || { echo "pacote de atualização não gerado ($PKG)" >&2; exit 1; }
OUT="dist/desktop/$VERSION"
mkdir -p "$OUT"
cp "$PKG" "$OUT/AI.Commerce.app.tar.gz" && cp "$PKG.sig" "$OUT/AI.Commerce.app.tar.gz.sig"
cp apps/desktop/src-tauri/target/release/bundle/dmg/*.dmg "$OUT/" 2>/dev/null || true
cat <<MSG
✓ versão $VERSION ($ARCH) em $OUT
Publicar no servidor (na VPS, pasta do projeto, depois de copiar $OUT para lá):
  C="docker compose -f infra/compose/docker-compose.yml -f infra/compose/production.yml --env-file infra/env/live.env"
  \$C cp $OUT/. api:/tmp/release-$VERSION/
  \$C exec api python -m aicommerce.manage publish-desktop-release --version $VERSION \\
      --notes "o que mudou" --artifact darwin-$ARCH=/tmp/release-$VERSION/AI.Commerce.app.tar.gz
  \$C exec -u root api rm -rf /tmp/release-$VERSION
Pacote do Windows: baixe o artefato "desktop-windows" do GitHub Actions e publique com --artifact windows-x86_64=…
(o build do Windows assinado para atualização exige os mesmos segredos TAURI_SIGNING_* no GitHub — ver deploy.yml).
Os apps instalados oferecem a versão nova em Configurações → Este computador → Atualizações.
MSG
