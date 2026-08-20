"""Directory poll: new files under WATCH_DIR/<pile_id>/ become incremental runs."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .config import settings
from .db import SessionLocal
from .engine import Engine
from .graph import invoke_run
from .models import Document, Pile
from .parsing import sha256_bytes
from .paths import fixtures_dir
from .rules import load_playbook

_started = False


def ingest_new_files(pile_id: str) -> dict:
    db = SessionLocal()
    try:
        pile = db.get(Pile, pile_id)
        if not pile:
            return {"started": False, "reason": "pile not found"}
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
            return {"started": False, "reason": "no new files"}
        p = fixtures_dir() / "playbooks" / "vendor-compliance.json"
        playbook = load_playbook(p.read_text(encoding="utf-8")) if p.exists() else {"rules": []}
        run = eng.start_run(pile, playbook, trigger="incremental", new_document_ids=new_ids)
        invoke_run(db, run)
        return {"started": True, "run_id": run.id, "new_document_ids": new_ids}
    finally:
        db.close()


def _loop():
    interval = float(os.environ.get("WATCH_POLL_SECONDS", "4"))
    while True:
        root = Path(settings.watch_dir)
        root.mkdir(parents=True, exist_ok=True)
        try:
            for child in root.iterdir():
                if child.is_dir():
                    ingest_new_files(child.name)
        except Exception:
            pass
        threading.Event().wait(interval)


def start_watcher() -> None:
    global _started
    if os.environ.get("WATCH_ENABLED", "1") in ("0", "false", "no"):
        return
    if _started:
        return
    _started = True
    Path(settings.watch_dir).mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=_loop, name="pile-watch", daemon=True)
    t.start()
