import importlib.util
from pathlib import Path
from types import SimpleNamespace

from aicommerce import db


def test_database_connections_have_bounded_libpq_timeout(monkeypatch):
    captured = {}

    def factory(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(db, "create_engine", factory)
    db.get_engine.__wrapped__()
    assert captured["connect_args"]["connect_timeout"] == 5
    assert captured["pool_pre_ping"] is True


def test_chaos_wait_observes_delayed_failure_and_does_not_treat_zero_as_failure():
    path = Path(__file__).resolve().parents[3] / "scripts/chaos_db.py"
    spec = importlib.util.spec_from_file_location("chaos_test", path)
    chaos = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chaos)
    clock = [0.0]
    chaos.time = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda n: clock.__setitem__(0, clock[0] + n))
    assert chaos.wait(lambda: clock[0] >= 12, 60) == 12
    assert chaos.wait(lambda: True, 60) == 0
    assert chaos.wait(lambda: False, 2) is None
