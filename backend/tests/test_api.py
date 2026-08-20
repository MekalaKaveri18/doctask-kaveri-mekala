from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db, make_engine
from app.main import app


def test_machine_can_drive_review_without_ui(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path}/api.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    pile = client.post("/piles", json={"name": "clean", "seed": "clean"}).json()
    run = client.post(f"/piles/{pile['id']}/runs", json={}).json()
    assert run["status"] == "awaiting_review"
    items = client.get(f"/runs/{run['id']}/review").json()
    decisions = {i["id"]: "approved" for i in items}
    after = client.post(f"/runs/{run['id']}/review", json={"decisions": decisions}).json()
    assert after["status"] == "committed"
    register = client.get(f"/runs/{run['id']}/register").json()
    assert any(s["section_id"] == "parties" for s in register)
    cost = client.get(f"/runs/{run['id']}/cost").json()
    assert cost["total_ms"] >= 0
    app.dependency_overrides.clear()
