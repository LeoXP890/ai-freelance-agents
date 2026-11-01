from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aicommerce.config import get_settings


@lru_cache
def get_engine() -> Engine:
    # libpq's default connect_timeout=0 is unbounded. A reconnect during a DB outage could
    # outlive the chaos window and skip the worker's failure/backoff path entirely.
    return create_engine(
        get_settings().database_url, pool_pre_ping=True, future=True, connect_args={"connect_timeout": 5}
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Dependência FastAPI: uma transação por request, commit no fim."""
    with session_scope() as session:
        yield session
