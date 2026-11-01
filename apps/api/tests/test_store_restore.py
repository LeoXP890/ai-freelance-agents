import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest


def restore_module():
    folder = Path(__file__).resolve().parents[3] / "infra/woo-store"
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location("woo_restore_full", folder / "restore_full.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(folder))


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape"])
def test_restore_rejects_archive_traversal(tmp_path, name):
    archive = tmp_path / "malicious.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(name)
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe"):
        restore_module().extract_files(archive, tmp_path / "target")


def test_restore_rejects_symlink(tmp_path):
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(ValueError, match="Links"):
        restore_module().extract_files(archive, tmp_path / "target")
