"""MCP server: same operations as REST, including explicit approval."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from .db import Base, SessionLocal, engine
from .engine import Engine
from .graph import invoke_run
from .models import Pile, RegisterSection, ReviewItem, Run, StageCost
from .paths import fixtures_dir
from .rules import load_playbook

mcp = FastMCP("vendor-analyst")


def _session():
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


@mcp.tool()
def create_pile(name: str, seed: str = "") -> str:
    db = _session()
    try:
        eng = Engine(db)
        if seed:
            folder = fixtures_dir() / "piles" / seed
            if not folder.exists():
                folder = fixtures_dir() / "piles" / f"{seed}-vendor"
            if not folder.exists():
                return json.dumps({"error": f"unknown seed {seed}"})
            pile = eng.seed_pile(name, folder)
            return json.dumps({"id": pile.id, "name": pile.name})
        pile = Pile(name=name)
        db.add(pile)
        db.commit()
        db.refresh(pile)
        return json.dumps({"id": pile.id, "name": pile.name})
    finally:
        db.close()


@mcp.tool()
def start_run(pile_id: str, trigger: str = "full") -> str:
    db = _session()
    try:
        pile = db.get(Pile, pile_id)
        if not pile:
            return json.dumps({"error": "pile not found"})
        p = fixtures_dir() / "playbooks" / "vendor-compliance.json"
        playbook = load_playbook(p.read_text(encoding="utf-8")) if p.exists() else {"rules": []}
        eng = Engine(db)
        run = eng.start_run(pile, playbook, trigger=trigger)
        invoke_run(db, run)
        return json.dumps({"id": run.id, "status": run.status})
    finally:
        db.close()


@mcp.tool()
def ingest_document(pile_id: str, filename: str, text: str) -> str:
    db = _session()
    try:
        pile = db.get(Pile, pile_id)
        if not pile:
            return json.dumps({"error": "pile not found"})
        doc = Engine(db).ingest_file(pile, filename, text.encode("utf-8"))
        return json.dumps({"id": doc.id, "filename": doc.filename})
    finally:
        db.close()


@mcp.tool()
def get_status(run_id: str) -> str:
    db = _session()
    try:
        run = db.get(Run, run_id)
        if not run:
            return json.dumps({"error": "run not found"})
        return json.dumps({"id": run.id, "status": run.status, "stage": run.current_stage})
    finally:
        db.close()


@mcp.tool()
def get_pending_review(run_id: str) -> str:
    db = _session()
    try:
        items = db.query(ReviewItem).filter(ReviewItem.run_id == run_id, ReviewItem.status == "pending").all()
        return json.dumps([{"id": i.id, "type": i.item_type, "title": i.title, "body": i.body} for i in items])
    finally:
        db.close()


@mcp.tool()
def approve_items(run_id: str, item_ids: list[str]) -> str:
    db = _session()
    try:
        run = db.get(Run, run_id)
        if not run:
            return json.dumps({"error": "run not found"})
        Engine(db).apply_review(run, {i: "approved" for i in item_ids})
        return json.dumps({"id": run.id, "status": run.status})
    finally:
        db.close()


@mcp.tool()
def reject_items(run_id: str, item_ids: list[str]) -> str:
    db = _session()
    try:
        run = db.get(Run, run_id)
        if not run:
            return json.dumps({"error": "run not found"})
        Engine(db).apply_review(run, {i: "rejected" for i in item_ids})
        return json.dumps({"id": run.id, "status": run.status})
    finally:
        db.close()


@mcp.tool()
def get_register(run_id: str) -> str:
    db = _session()
    try:
        run = db.get(Run, run_id)
        if not run:
            return json.dumps({"error": "run not found"})
        if run.status != "committed":
            return json.dumps({"error": "not committed; no success claimed"})
        rows = db.query(RegisterSection).filter(RegisterSection.run_id == run_id).all()
        return json.dumps([{"section_id": r.section_id, "body": r.body, "hash": r.content_hash} for r in rows])
    finally:
        db.close()


@mcp.tool()
def get_cost(run_id: str) -> str:
    db = _session()
    try:
        rows = db.query(StageCost).filter(StageCost.run_id == run_id).all()
        return json.dumps([{"stage": r.stage, "ms": r.ms, "usd": r.usd, "llm_calls": r.llm_calls} for r in rows])
    finally:
        db.close()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
