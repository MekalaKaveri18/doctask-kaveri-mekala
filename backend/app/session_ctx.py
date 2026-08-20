from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy.orm import Session

_current_db: ContextVar[Session | None] = ContextVar("vendor_analyst_db", default=None)


def current_db() -> Session:
    db = _current_db.get()
    if db is None:
        raise RuntimeError("no database session bound to this agent run")
    return db


@contextmanager
def bind_db(db: Session):
    token = _current_db.set(db)
    try:
        yield db
    finally:
        _current_db.reset(token)
