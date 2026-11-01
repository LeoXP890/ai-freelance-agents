"""Atualização automática do app desktop servida pela API (UXD-021): manifesto no formato do updater do Tauri,
só versões mais novas e não revogadas, por plataforma; versões imutáveis; pacote servido com cache eterno."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import delete

from aicommerce import desktop_updates as du
from aicommerce import manage
from aicommerce.db import session_scope
from aicommerce.models import DesktopArtifact, DesktopRelease


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        with session_scope() as db:
            db.execute(delete(DesktopArtifact))
            db.execute(delete(DesktopRelease))

    wipe()
    yield
    wipe()


def make_pkg(tmp_path, name="AI Commerce.app.tar.gz", sig="assinatura-minisign"):
    d = tmp_path / uuid.uuid4().hex[:6]
    d.mkdir()
    p = d / name
    p.write_bytes(b"pacote" + uuid.uuid4().bytes)
    p.with_name(p.name + ".sig").write_text(sig + "\n")
    return p


def pub(tmp_path, version, platform="darwin-aarch64", notes=""):
    with session_scope() as db:
        (art,) = du.publish(db, version=version, notes=notes, artifacts={platform: make_pkg(tmp_path)}, actor="t")
        return du.artifact_url(art), art.sha256


def test_manifest_follows_tauri_format_and_picks_highest_newer_version(client, tmp_path):
    pkg = make_pkg(tmp_path, sig="sig-0.2.0")
    cmd = [
        "publish-desktop-release",
        "--version",
        "0.2.0",
        "--notes",
        "Correções",
        "--artifact",
        f"darwin-aarch64={pkg}",
    ]
    assert manage.main(cmd) == 0
    pub(tmp_path, "0.10.0")  # numérica: 0.10.0 > 0.9.x > 0.2.0
    pub(tmp_path, "0.9.5")
    m = client.get("/v1/desktop/update/darwin/aarch64/0.1.0").json()
    assert m["version"] == "0.10.0" and m["signature"] == "assinatura-minisign" and m["pub_date"].endswith("Z")
    assert m["url"].startswith("http://localhost:8080/api/v1/desktop/artifacts/")
    assert client.get("/v1/desktop/update/darwin/aarch64/0.10.0").status_code == 204  # já está na mais nova
    assert client.get("/v1/desktop/update/windows/x86_64/0.1.0").status_code == 204  # sem pacote para Windows
    assert client.get("/v1/desktop/update/plan9/mips/0.1.0").status_code == 204
    assert client.get("/v1/desktop/update/darwin/aarch64/nao-versao").status_code == 400


def test_artifact_is_served_immutable_and_matches_hash(client, tmp_path):
    url, sha = pub(tmp_path, "0.2.0", "windows-x86_64")
    got = client.get(url.split("/api", 1)[1])
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == sha
    assert "immutable" in got.headers["cache-control"]
    assert client.get(f"/v1/desktop/artifacts/{sha}/outro-nome.exe").status_code == 404


def test_versions_are_immutable_and_inputs_validated(tmp_path):
    pub(tmp_path, "0.2.0")
    with session_scope() as db, pytest.raises(du.ReleaseError, match="imutáveis"):
        du.publish(db, version="0.2.0", notes="", artifacts={"darwin-aarch64": make_pkg(tmp_path)}, actor="t")
    for version, arts, msg in (
        ("0.3.0", {"beos-x86": make_pkg(tmp_path)}, "plataforma"),
        ("0.3.0", {"darwin-aarch64": tmp_path / "nao-existe"}, "faltam"),
        ("v2", {"darwin-aarch64": make_pkg(tmp_path)}, "versão inválida"),
        ("0.3.0", {}, "ao menos um"),
    ):
        with session_scope() as db, pytest.raises(du.ReleaseError, match=msg):
            du.publish(db, version=version, notes="", artifacts=arts, actor="t")


def test_revoked_version_is_no_longer_offered(client, tmp_path):
    pub(tmp_path, "0.2.0")
    pub(tmp_path, "0.3.0")
    assert manage.main(["revoke-desktop-release", "--version", "0.3.0"]) == 0
    assert client.get("/v1/desktop/update/darwin/aarch64/0.1.0").json()["version"] == "0.2.0"
    assert manage.main(["revoke-desktop-release", "--version", "0.2.0"]) == 0
    assert client.get("/v1/desktop/update/darwin/aarch64/0.1.0").status_code == 204
