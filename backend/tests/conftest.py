from __future__ import annotations

import os

os.environ.setdefault("WATCH_ENABLED", "0")
os.environ.setdefault("LLM_PROVIDER", "fake")

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import Base, make_engine
from app.paths import fixtures_dir
from app.rules import load_playbook


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def playbook():
    p = fixtures_dir() / "playbooks" / "vendor-compliance.json"
    return load_playbook(p.read_text(encoding="utf-8"))
