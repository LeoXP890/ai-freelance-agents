import psycopg
import pytest

from aicommerce.dbroles import APP_ROLE, DEV_PASSWORD, ensure_app_role
from tests.conftest import PG_HOST, PG_PORT, TEST_DB


@pytest.fixture(scope="module")
def app_conn():
    ensure_app_role(DEV_PASSWORD)
    with psycopg.connect(
        host=PG_HOST, port=PG_PORT, user=APP_ROLE, password=DEV_PASSWORD, dbname=TEST_DB, autocommit=True
    ) as c:
        yield c


def test_app_role_can_do_dml(app_conn):
    assert app_conn.execute("SELECT count(*) FROM agents").fetchone()[0] == 13
    app_conn.execute("UPDATE agent_presence SET state = state WHERE agent_id = 'atlas'")


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete",
        "TRUNCATE office_events",
        "DROP TABLE ledger_entries",
        "CREATE TABLE intruso (id int)",
    ],
)
def test_app_role_cannot_tamper_or_change_schema(app_conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(sql)


def test_append_only_still_enforced_for_app(app_conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
        app_conn.execute("UPDATE audit_events SET action = 'x'")


def test_app_role_is_not_owner_nor_superuser(app_conn):
    row = app_conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
    ).fetchone()
    assert row == (False, False, False)
    owned = app_conn.execute("SELECT count(*) FROM pg_tables WHERE tableowner = %s", (APP_ROLE,)).fetchone()[0]
    assert owned == 0
