"""Papel de banco da aplicação com mínimo privilégio (INF-007; SDD §5, §16).

O dono das tabelas (usado só pelas migrações) é separado do papel que a API e o worker usam:
* `aicommerce_app`: SELECT/INSERT/UPDATE/DELETE e uso de sequências; sem TRUNCATE, sem DDL e sem posse —
  portanto NÃO consegue desativar os triggers append-only nem alterar o schema.
Idempotente. Uso (como dono, após `alembic upgrade head`): python -m aicommerce.dbroles
A senha vem de AIC_DB_APP_PASSWORD (obrigatória fora de DRY_RUN).
"""

from __future__ import annotations

import os

from sqlalchemy import text

from aicommerce.config import get_settings
from aicommerce.db import get_engine

APP_ROLE = "aicommerce_app"
DEV_PASSWORD = "aicommerce_app_dev"


def ensure_app_role(password: str | None = None) -> None:
    s = get_settings()
    pwd = password or os.environ.get("AIC_DB_APP_PASSWORD") or (DEV_PASSWORD if s.is_dry_run else None)
    if not pwd:
        raise RuntimeError("AIC_DB_APP_PASSWORD obrigatória fora de DRY_RUN")
    with get_engine().begin() as c:
        db = c.execute(text("SELECT current_database()")).scalar_one()
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).first()
        quoted = pwd.replace("'", "''")
        if not exists:
            c.execute(
                text(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '{quoted}'")
            )
        else:
            c.execute(text(f"ALTER ROLE {APP_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{quoted}'"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{db}" TO {APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        c.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {APP_ROLE}"))
        c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        c.execute(text(f"REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
        c.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE "
                f"ON TABLES TO {APP_ROLE}"
            )
        )
        c.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"))


if __name__ == "__main__":
    ensure_app_role()
    print(f"papel {APP_ROLE} configurado (sem posse, sem TRUNCATE, sem DDL)")
