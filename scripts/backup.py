#!/usr/bin/env python3
"""Backup criptografado e teste de restauração (INF-006; SDD §5 RPO<=24h/RTO<=4h, §17).

  backup        pg_dump (formato custom) do Postgres do compose → cifra AES-256 (openssl, PBKDF2) → manifesto
                (contagem de linhas por tabela, versão do schema, sha256 do arquivo cifrado) → retenção.
  restore-test  decifra o backup mais recente, restaura num banco temporário, confere versão do schema,
                contagens e a cadeia de auditoria, mede o tempo (RTO) e a idade do backup (RPO), apaga o banco
                temporário e grava o relatório em progress/test-reports/restore_check.json.

  offsite       copia os backups cifrados (e manifestos) para fora da VPS com rclone (S3, Backblaze B2, Google
                Drive…): destino em AIC_BACKUP_REMOTE (ex.: b2:aicommerce-backups), configuração do rclone em
                infra/env/secrets/rclone.conf. Só sobe arquivos já cifrados; confere que o mais recente chegou.

Uso (de apps/api): uv run python ../../scripts/backup.py backup | restore-test | offsite
Ambiente: AIC_ENV_FILE (padrão infra/env/dry_run.env; na VPS infra/env/live.env) e AIC_COMPOSE_PRODUCTION=1 para
incluir infra/compose/production.yml. Chave: AIC_BACKUP_PASSPHRASE_FILE; em DRY_RUN, sem a variável, gera
backups/.passphrase local (fora do git).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_FILE = pathlib.Path(os.environ.get("AIC_ENV_FILE", ROOT / "infra/env/dry_run.env"))


def env_setting(key: str, default: str) -> str:
    """Use the same Compose env file as the selected DB unless overridden by systemd."""
    if key in os.environ:
        return os.environ[key]
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if not raw.strip().startswith("#"):
                found, sep, value = raw.partition("=")
                if sep and found.strip() == key:
                    return value.strip().strip('"').strip("'")
    return default


STACK = env_setting("AIC_STACK", "dryrun")
if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", STACK):
    raise ValueError("AIC_STACK inválido para backup")
# Never share a dump/passphrase/retention namespace between live and staging.
BACKUPS = ROOT / "backups" / STACK
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra/compose/docker-compose.yml"),
           *(["-f", str(ROOT / "infra/compose/production.yml")] if os.environ.get("AIC_COMPOSE_PRODUCTION") else []),
           "--env-file", str(ENV_FILE)]
RCLONE_IMAGE = "rclone/rclone:1.68.2"
RCLONE_CONF = ROOT / "infra/env/secrets/rclone.conf"
PGUSER = env_setting("POSTGRES_USER", "aicommerce")
PGDB = env_setting("POSTGRES_DB", "aicommerce")
RESTORE_DB = f"aicommerce_{STACK.replace('-', '_')}_restore_check"
KEEP = int(os.environ.get("AIC_BACKUP_KEEP", "14"))


def psql(sql: str, db: str = PGDB) -> str:
    return subprocess.run([*COMPOSE, "exec", "-T", "postgres", "psql", "-U", PGUSER, "-d", db, "-tAc", sql],
                          check=True, capture_output=True, text=True).stdout.strip()


def counts(db: str) -> dict[str, int]:
    tables = psql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1", db).split()
    return {t: int(psql(f'SELECT count(*) FROM "{t}"', db)) for t in tables}


def passphrase() -> pathlib.Path:
    env = env_setting("AIC_BACKUP_PASSPHRASE_FILE", "")
    if env:
        return pathlib.Path(env)
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    BACKUPS.chmod(0o700)
    p = BACKUPS / ".passphrase"
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as out:
            out.write(secrets.token_urlsafe(48))
    return p


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup() -> dict:
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    BACKUPS.chmod(0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw, enc = BACKUPS / f"aicommerce-{stamp}.dump", BACKUPS / f"aicommerce-{stamp}.dump.enc"
    t0 = time.monotonic()
    manifest = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "database": PGDB,
                "stack": STACK,
                "schema_version": psql("SELECT version_num FROM alembic_version"), "row_counts": counts(PGDB)}
    try:
        fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            subprocess.run([*COMPOSE, "exec", "-T", "postgres", "pg_dump", "-U", PGUSER, "-Fc", PGDB], check=True, stdout=f)
        subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-in", str(raw),
                        "-out", str(enc), "-pass", f"file:{passphrase()}"], check=True)
    except BaseException:
        enc.unlink(missing_ok=True)
        raise
    finally:
        raw.unlink(missing_ok=True)  # dumps plaintext are always deleted, even if encryption fails
    manifest |= {"file": enc.name, "sha256": sha256(enc), "bytes": enc.stat().st_size,
                 "cipher": "aes-256-cbc+pbkdf2(200000)", "duration_s": round(time.monotonic() - t0, 2)}
    (BACKUPS / f"aicommerce-{stamp}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for old in sorted(BACKUPS.glob("aicommerce-*.dump.enc"))[:-KEEP]:  # retenção
        old.unlink()
        old.with_name(old.name.replace(".dump.enc", ".manifest.json")).unlink(missing_ok=True)
    print(json.dumps({k: v for k, v in manifest.items() if k != "row_counts"}, indent=2))
    return manifest


def restore_test() -> dict:
    encs = sorted(BACKUPS.glob("aicommerce-*.dump.enc"))
    if not encs:
        sys.exit("nenhum backup encontrado — rode 'backup' antes")
    enc = encs[-1]
    manifest = json.loads(enc.with_name(enc.name.replace(".dump.enc", ".manifest.json")).read_text())
    # Fail BEFORE decryption and DB creation when a file was copied across stacks
    # or tampered with. Never test live restores against a staging database.
    if manifest.get("stack") != STACK or manifest.get("database") != PGDB:
        raise RuntimeError("manifesto pertence a outra stack/banco; restauração recusada")
    checks: dict = {"file": enc.name, "sha256_ok": sha256(enc) == manifest["sha256"]}
    if not checks["sha256_ok"]:
        raise RuntimeError("checksum do backup inválido; restauração recusada")
    t0 = time.monotonic()
    raw = BACKUPS / "restore.tmp.dump"
    try:
        subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-in", str(enc),
                        "-out", str(raw), "-pass", f"file:{passphrase()}"], check=True)
        psql(f"DROP DATABASE IF EXISTS {RESTORE_DB} WITH (FORCE)", "postgres")
        psql(f"CREATE DATABASE {RESTORE_DB}", "postgres")
        with raw.open("rb") as f:
            subprocess.run([*COMPOSE, "exec", "-T", "postgres", "pg_restore", "-U", PGUSER, "-d", RESTORE_DB,
                            "--no-owner", "--exit-on-error"], check=True, stdin=f)
        checks["rto_s"] = round(time.monotonic() - t0, 2)
        checks["schema_version_ok"] = psql("SELECT version_num FROM alembic_version", RESTORE_DB) == manifest["schema_version"]
        restored = counts(RESTORE_DB)
        diff = {t: (n, restored.get(t)) for t, n in manifest["row_counts"].items() if restored.get(t) != n}
        checks["row_counts_ok"], checks["row_count_diffs"] = not diff, diff
        port = env_setting("AIC_PG_PORT", "55440")
        password = env_setting("POSTGRES_PASSWORD", "aicommerce")
        os.environ["AIC_DATABASE_URL"] = f"postgresql+psycopg://{PGUSER}:{password}@localhost:{port}/{RESTORE_DB}"
        from aicommerce.db import get_engine, session_scope  # importado só agora: aponta para o banco restaurado
        from aicommerce.governance.audit import verify_chain

        with session_scope() as db:
            ok, bad = verify_chain(db)
        get_engine().dispose()
        checks["audit_chain_ok"], checks["audit_first_invalid"] = ok, bad
    finally:
        raw.unlink(missing_ok=True)
        psql(f"DROP DATABASE IF EXISTS {RESTORE_DB} WITH (FORCE)", "postgres")
    age_h = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(manifest["created_at"])).total_seconds() / 3600
    checks["rpo_hours"] = round(age_h, 3)
    checks["slo"] = {"rpo_ok": age_h <= 24, "rto_ok": checks["rto_s"] <= 4 * 3600}
    checks["ok"] = all([checks["sha256_ok"], checks["schema_version_ok"], checks["row_counts_ok"],
                        checks["audit_chain_ok"], checks["slo"]["rpo_ok"], checks["slo"]["rto_ok"]])
    checks["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (ROOT / "progress/test-reports/restore_check.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))
    return checks


def offsite_command(remote: str) -> list[str]:
    """rclone em container (sem instalar nada na VPS); só arquivos cifrados e manifestos sobem."""
    return ["docker", "run", "--rm", "-v", f"{BACKUPS}:/data:ro", "-v", f"{RCLONE_CONF}:/config/rclone/rclone.conf:ro",
            RCLONE_IMAGE, "copy", "/data", remote, "--include", "aicommerce-*.dump.enc",
            "--include", "aicommerce-*.manifest.json", "--immutable"]


def offsite() -> dict:
    remote = env_setting("AIC_BACKUP_REMOTE", "").strip()
    if not remote:
        sys.exit("defina AIC_BACKUP_REMOTE (ex.: b2:aicommerce-backups) e infra/env/secrets/rclone.conf")
    if not RCLONE_CONF.exists():
        sys.exit(f"falta {RCLONE_CONF} — crie com: docker run --rm -it -v $PWD/infra/env/secrets:/config/rclone "
                 f"{RCLONE_IMAGE} config")
    encs = sorted(BACKUPS.glob("aicommerce-*.dump.enc"))
    if not encs:
        sys.exit("nenhum backup encontrado — rode 'backup' antes")
    # Always isolate remote objects by stack, even if both envs share an rclone account.
    remote = remote.rstrip("/") + "/" + STACK
    subprocess.run(offsite_command(remote), check=True)
    listed = subprocess.run(["docker", "run", "--rm", "-v", f"{RCLONE_CONF}:/config/rclone/rclone.conf:ro",
                             RCLONE_IMAGE, "lsf", remote, "--include", encs[-1].name],
                            check=True, capture_output=True, text=True).stdout.split()
    result = {"remote": remote, "latest": encs[-1].name, "present_offsite": encs[-1].name in listed,
              "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    print(json.dumps(result, indent=2))
    if not result["present_offsite"]:
        sys.exit("o backup mais recente não apareceu no destino externo")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backup", "restore-test", "offsite"])
    a = ap.parse_args()
    if a.cmd == "backup":
        backup()
    elif a.cmd == "offsite":
        offsite()
    else:
        sys.exit(0 if restore_test()["ok"] else 1)
