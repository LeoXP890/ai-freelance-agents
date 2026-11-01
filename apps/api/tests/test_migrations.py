from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from aicommerce.db import get_engine
from aicommerce.models import Base


def test_migrations_match_models():
    """Nenhuma diferença entre o schema migrado e os modelos (evita migração esquecida)."""
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_monetary_columns_are_bigint():
    for table in ("ledger_entries", "budget_reservations", "budget_policies", "approval_proposals"):
        for col in Base.metadata.tables[table].columns:
            if col.name.endswith("_minor"):
                assert col.type.__class__.__name__ == "BigInteger", f"{table}.{col.name}"


def test_0021_seller_profile_preserves_existing_records(monkeypatch):
    """Adaptação PF (ADR 0006): a migração cria perfil PF e matriz vazia sem tocar nos registros existentes; o
    downgrade remove só as tabelas novas. Banco próprio, criado e apagado pelo teste."""
    import os

    import psycopg
    from alembic import command
    from alembic.config import Config

    from aicommerce.config import get_settings
    from tests.conftest import API_DIR, PG_HOST, PG_PASS, PG_PORT, PG_USER

    name = "aicommerce_mig_0021"
    admin = dict(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, autocommit=True)
    with psycopg.connect(dbname="postgres", **admin) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {name}")
    monkeypatch.setattr(
        get_settings(), "database_url", f"postgresql+psycopg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{name}"
    )
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "migrations"))
    try:
        command.upgrade(cfg, "0020")
        with psycopg.connect(dbname=name, **admin) as c:
            c.execute(
                "INSERT INTO certification_gates (code, title, expected_evidence, responsible, blocks, state, scope) "
                "VALUES ('LEG-01', 'Empresa e elegibilidade', 'CNPJ…', 'Proprietário', 'Toda venda real', "
                "'IN_REVIEW', '{}')"
            )
        command.upgrade(cfg, "head")
        with psycopg.connect(dbname=name, **admin) as c:
            assert c.execute("SELECT state, title FROM certification_gates WHERE code='LEG-01'").fetchone() == (
                "IN_REVIEW",
                "Empresa e elegibilidade",
            )  # registro histórico preservado (o texto PF é apresentado pela API, não regravado)
            assert c.execute("SELECT person_type, tax_id_status FROM seller_profile").fetchall() == [
                ("PERSON", "NOT_COLLECTED")
            ]
            assert c.execute("SELECT count(*) FROM channel_eligibility").fetchone() == (0,)
        command.downgrade(cfg, "0020")
        with psycopg.connect(dbname=name, **admin) as c:
            assert c.execute("SELECT count(*) FROM certification_gates").fetchone() == (1,)
    finally:
        monkeypatch.undo()
        with psycopg.connect(dbname="postgres", **admin) as c:
            c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
