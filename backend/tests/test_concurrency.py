from __future__ import annotations

import json
import threading

from sqlalchemy.orm import sessionmaker

from app.db import Base, make_engine
from app.engine import Engine
from app.models import Pile, Run

from tests.helpers import seed


def test_concurrent_piles_stay_isolated(tmp_path, playbook):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path}/c.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    errors: list[Exception] = []
    run_ids: list[str] = []

    def go(name: str):
        s = Session()
        try:
            e = Engine(s)
            pile = seed(e, name)
            run = e.start_run(pile, playbook)
            e.advance(run)
            run_ids.append(run.id)
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)
        finally:
            s.close()

    t1 = threading.Thread(target=go, args=("acme-vendor",))
    t2 = threading.Thread(target=go, args=("clean-vendor",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert errors == [], errors
    s = Session()
    try:
        runs = s.query(Run).all()
        assert len(runs) == 2
        assert len({r.pile_id for r in runs}) == 2
        states = [json.loads(r.state_json or "{}") for r in runs]
        names = [{d["filename"] for d in st.get("documents") or []} for st in states]
        assert any("MSA-2024-001.txt" in n for n in names)
        assert any("MSA-CLEAN-001.txt" in n for n in names)
        assert not all("MSA-2024-001.txt" in n and "MSA-CLEAN-001.txt" in n for n in names)
    finally:
        s.close()


def test_same_pile_two_runs_serialized(tmp_path, playbook):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path}/same.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s0 = Session()
    pile = seed(Engine(s0), "clean-vendor")
    pile_id = pile.id
    s0.close()
    errors: list[Exception] = []

    def go():
        s = Session()
        try:
            e = Engine(s)
            pile = s.get(Pile, pile_id)
            run = e.start_run(pile, playbook)
            e.advance(run)
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)
        finally:
            s.close()

    t1 = threading.Thread(target=go)
    t2 = threading.Thread(target=go)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert errors == [], errors
    s = Session()
    try:
        runs = s.query(Run).filter(Run.pile_id == pile_id).all()
        assert len(runs) == 2
        assert {r.status for r in runs} <= {"awaiting_review", "running", "paused"}
    finally:
        s.close()
