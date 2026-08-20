from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, get_db
from .engine import Engine
from .graph import invoke_run
from .models import Document, Event, Pile, RegisterSection, ReviewItem, Run, StageCost
from .parsing import sha256_bytes
from .paths import fixtures_dir
from .rules import load_playbook

app = FastAPI(title="Vendor Analyst", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    from .watcher import start_watcher

    start_watcher()


class CreatePile(BaseModel):
    name: str
    seed: str | None = None


class StartRun(BaseModel):
    playbook: dict | None = None
    trigger: str = "full"
    new_document_ids: list[str] = Field(default_factory=list)


class ReviewDecision(BaseModel):
    decisions: dict[str, str]


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/piles")
def create_pile(body: CreatePile, db: Session = Depends(get_db)):
    eng = Engine(db)
    if body.seed:
        folder = fixtures_dir() / "piles" / body.seed
        if not folder.exists():
            folder = fixtures_dir() / "piles" / f"{body.seed}-vendor"
        if not folder.exists():
            raise HTTPException(404, f"unknown seed {body.seed}")
        playbook_path = fixtures_dir() / "playbooks" / "vendor-compliance.json"
        pile = eng.seed_pile(body.name, folder)
        return {"id": pile.id, "name": pile.name, "playbook": playbook_path.name}
    pile = Pile(name=body.name)
    db.add(pile)
    db.commit()
    db.refresh(pile)
    return {"id": pile.id, "name": pile.name}


@app.get("/piles")
def list_piles(db: Session = Depends(get_db)):
    piles = db.query(Pile).all()
    return [{"id": p.id, "name": p.name, "version": p.version} for p in piles]


@app.post("/piles/{pile_id}/documents")
async def upload_document(pile_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    pile = db.get(Pile, pile_id)
    if not pile:
        raise HTTPException(404, "pile not found")
    data = await file.read()
    doc = Engine(db).ingest_file(pile, file.filename or "upload.bin", data)
    return {"id": doc.id, "filename": doc.filename, "sha256": doc.sha256}


@app.get("/piles/{pile_id}/documents")
def list_docs(pile_id: str, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.pile_id == pile_id).all()
    return [{"id": d.id, "filename": d.filename, "kind": d.kind} for d in docs]


@app.get("/piles/{pile_id}/runs")
def list_runs(pile_id: str, db: Session = Depends(get_db)):
    pile = db.get(Pile, pile_id)
    if not pile:
        raise HTTPException(404, "pile not found")
    runs = db.query(Run).filter(Run.pile_id == pile_id).order_by(Run.created_at.desc()).all()
    return [{"id": r.id, "status": r.status, "stage": r.current_stage, "trigger": r.trigger} for r in runs]


@app.post("/piles/{pile_id}/runs")
def start_run(pile_id: str, body: StartRun, db: Session = Depends(get_db)):
    pile = db.get(Pile, pile_id)
    if not pile:
        raise HTTPException(404, "pile not found")
    playbook = body.playbook
    if playbook is None:
        p = fixtures_dir() / "playbooks" / "vendor-compliance.json"
        playbook = load_playbook(p.read_text(encoding="utf-8")) if p.exists() else {"rules": []}
    eng = Engine(db)
    run = eng.start_run(pile, playbook, trigger=body.trigger, new_document_ids=body.new_document_ids)
    invoke_run(db, run)
    db.refresh(run)
    return _run_out(db, run)


@app.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return _run_out(db, run)


@app.post("/runs/{run_id}/kill")
def kill_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    Engine(db).kill(run)
    return {"id": run.id, "status": "kill_requested"}


@app.post("/runs/{run_id}/resume")
def resume_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    run.kill_requested = 0
    run.status = "running"
    db.commit()
    invoke_run(db, run)
    db.refresh(run)
    return _run_out(db, run)


@app.get("/runs/{run_id}/review")
def pending_review(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    items = db.query(ReviewItem).filter(ReviewItem.run_id == run_id).all()
    return [_item_out(i) for i in items]


@app.post("/runs/{run_id}/review")
def apply_review(run_id: str, body: ReviewDecision, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    for v in body.decisions.values():
        if v not in ("approved", "rejected"):
            raise HTTPException(400, "decisions must be approved or rejected")
    Engine(db).apply_review(run, body.decisions)
    db.refresh(run)
    return _run_out(db, run)


@app.get("/runs/{run_id}/register")
def get_register(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "committed":
        raise HTTPException(409, "register is not committed; success is not claimed")
    rows = db.query(RegisterSection).filter(RegisterSection.run_id == run_id).all()
    return [
        {
            "section_id": r.section_id,
            "title": r.title,
            "body": r.body,
            "source_ids": json.loads(r.source_ids or "[]"),
            "content_hash": r.content_hash,
            "unchanged": bool(r.unchanged),
        }
        for r in rows
    ]


@app.get("/runs/{run_id}/cost")
def get_cost(run_id: str, db: Session = Depends(get_db)):
    rows = db.query(StageCost).filter(StageCost.run_id == run_id).all()
    stages = [
        {
            "stage": r.stage,
            "ms": r.ms,
            "llm_calls": r.llm_calls,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "usd": r.usd,
        }
        for r in rows
    ]
    return {
        "stages": stages,
        "total_ms": sum(s["ms"] for s in stages),
        "total_usd": sum(s["usd"] for s in stages),
        "total_llm_calls": sum(s["llm_calls"] for s in stages),
    }


@app.post("/piles/{pile_id}/watch-ingest")
def watch_ingest(pile_id: str, db: Session = Depends(get_db)):
    """Pick up new files from WATCH_DIR/<pile_id> and start an incremental run."""
    pile = db.get(Pile, pile_id)
    if not pile:
        raise HTTPException(404, "pile not found")
    folder = Path(settings.watch_dir) / pile_id
    folder.mkdir(parents=True, exist_ok=True)
    eng = Engine(db)
    new_ids = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        data = path.read_bytes()
        digest = sha256_bytes(data)
        existing = (
            db.query(Document)
            .filter(Document.pile_id == pile.id, Document.sha256 == digest)
            .one_or_none()
        )
        if existing:
            continue
        doc = eng.ingest_file(pile, path.name, data)
        new_ids.append(doc.id)
    if not new_ids:
        return {"started": False, "reason": "no files"}
    p = fixtures_dir() / "playbooks" / "vendor-compliance.json"
    playbook = load_playbook(p.read_text(encoding="utf-8")) if p.exists() else {"rules": []}
    run = eng.start_run(pile, playbook, trigger="incremental", new_document_ids=new_ids)
    invoke_run(db, run)
    return _run_out(db, run)


def _item_out(i: ReviewItem) -> dict:
    return {
        "id": i.id,
        "type": i.item_type,
        "title": i.title,
        "body": i.body,
        "locator": i.locator,
        "status": i.status,
        "payload": json.loads(i.payload_json or "{}"),
    }


def _run_out(db: Session, run: Run) -> dict:
    events = db.query(Event).filter(Event.run_id == run.id).order_by(Event.created_at).all()
    items = db.query(ReviewItem).filter(ReviewItem.run_id == run.id).all()
    state = json.loads(run.state_json or "{}")
    return {
        "id": run.id,
        "pile_id": run.pile_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "trigger": run.trigger,
        "path_decisions": state.get("path_decisions") or [],
        "events": [{"stage": e.stage, "message": e.message, "at": e.created_at.isoformat()} for e in events],
        "pending_review": sum(1 for i in items if i.status == "pending"),
        "error": run.error,
    }
