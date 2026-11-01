"""No staging backup can overwrite, restore or purge a live database archive."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/backup.py"


def load_backup(monkeypatch, tmp_path, stack="staging", db="aicommerce_staging"):
    env = tmp_path / f"{stack}.env"
    env.write_text(
        f"AIC_STACK={stack}\nPOSTGRES_DB={db}\nPOSTGRES_USER=aicommerce\n"
        "POSTGRES_PASSWORD=test-only\nAIC_PG_PORT=55441\n",
        encoding="utf-8",
    )
    for key in ("AIC_STACK", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "AIC_BACKUP_PASSPHRASE_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AIC_ENV_FILE", str(env))
    spec = importlib.util.spec_from_file_location(f"backup_{stack}", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_staging_and_live_never_share_backup_storage_or_restore_database(monkeypatch, tmp_path):
    stage = load_backup(monkeypatch, tmp_path, "staging", "aicommerce_staging")
    live = load_backup(monkeypatch, tmp_path, "live", "aicommerce_live")
    assert stage.BACKUPS != live.BACKUPS
    assert stage.BACKUPS.name == "staging" and live.BACKUPS.name == "live"
    assert stage.RESTORE_DB != live.RESTORE_DB
    assert stage.PGDB == "aicommerce_staging"
    assert live.PGDB == "aicommerce_live"
    assert "staging.env" in str(stage.ENV_FILE)
    assert stage.env_setting("AIC_PG_PORT", "55440") == "55441"


@pytest.mark.parametrize(
    ("manifest_stack", "manifest_db"),
    [("live", "aicommerce_staging"), ("staging", "aicommerce_live")],
)
def test_restore_rejects_other_stack_before_touching_database(monkeypatch, tmp_path, manifest_stack, manifest_db):
    mod = load_backup(monkeypatch, tmp_path)
    mod.BACKUPS = tmp_path / "backups-staging"
    mod.BACKUPS.mkdir()
    enc = mod.BACKUPS / "aicommerce-20261025T230000Z.dump.enc"
    enc.write_bytes(b"not-even-encrypted")
    enc.with_name(enc.name.replace(".dump.enc", ".manifest.json")).write_text(
        json.dumps(
            {
                "stack": manifest_stack,
                "database": manifest_db,
                "sha256": hashlib.sha256(enc.read_bytes()).hexdigest(),
            }
        )
    )
    monkeypatch.setattr(mod, "psql", lambda *args, **kwargs: pytest.fail("DB must not be touched"))
    with pytest.raises(RuntimeError, match="outra stack/banco"):
        mod.restore_test()


def test_restore_rejects_tampered_ciphertext_before_decrypt(monkeypatch, tmp_path):
    mod = load_backup(monkeypatch, tmp_path)
    mod.BACKUPS = tmp_path / "backups-staging"
    mod.BACKUPS.mkdir()
    enc = mod.BACKUPS / "aicommerce-20261025T230000Z.dump.enc"
    enc.write_bytes(b"tampered")
    enc.with_name(enc.name.replace(".dump.enc", ".manifest.json")).write_text(
        json.dumps({"stack": mod.STACK, "database": mod.PGDB, "sha256": "0" * 64})
    )
    monkeypatch.setattr(mod, "psql", lambda *args, **kwargs: pytest.fail("DB must not be touched"))
    with pytest.raises(RuntimeError, match="checksum"):
        mod.restore_test()


def test_failed_encryption_does_not_leave_plaintext_dump(monkeypatch, tmp_path):
    mod = load_backup(monkeypatch, tmp_path)
    mod.BACKUPS = tmp_path / "backups-staging"
    monkeypatch.setattr(mod, "psql", lambda *args, **kwargs: "base")
    monkeypatch.setattr(mod, "counts", lambda *args, **kwargs: {})

    def broken_encrypt(args, **kwargs):
        if "pg_dump" in args:
            kwargs["stdout"].write(b"private customer data")
            return None
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(mod.subprocess, "run", broken_encrypt)
    with pytest.raises(subprocess.CalledProcessError):
        mod.backup()
    assert not list(mod.BACKUPS.glob("*.dump"))
    assert not list(mod.BACKUPS.glob("*.dump.enc"))


def test_invalid_stack_name_cannot_escape_backup_root(monkeypatch, tmp_path):
    env = tmp_path / "malicious.env"
    env.write_text("AIC_STACK=../../live\n")
    monkeypatch.setenv("AIC_ENV_FILE", str(env))
    monkeypatch.delenv("AIC_STACK", raising=False)
    spec = importlib.util.spec_from_file_location("backup_invalid", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    with pytest.raises(ValueError, match="AIC_STACK inválido"):
        spec.loader.exec_module(mod)
