from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from .embeddings import dump_vec, hash_embedding
from .llm import get_llm, looks_like_injection
from .models import (
    Checkpoint,
    Chunk,
    Document,
    Event,
    Pile,
    RegisterSection,
    ReviewItem,
    Run,
    StageCost,
    new_id,
    utcnow,
)
from .parsing import parse_bytes, sha256_bytes
from .rules import evaluate_playbook, load_playbook

STAGES = ["ingest", "classify", "extract", "reconcile", "draft", "examine", "gate"]
pile_locks: dict[str, threading.Lock] = {}
pile_locks_guard = threading.Lock()


def lock_for(pile_id: str) -> threading.Lock:
    with pile_locks_guard:
        if pile_id not in pile_locks:
            pile_locks[pile_id] = threading.Lock()
        return pile_locks[pile_id]


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def log_event(db: Session, run: Run, stage: str, message: str) -> None:
    db.add(Event(run_id=run.id, stage=stage, message=message))
    db.commit()


def save_checkpoint(db: Session, run: Run, stage: str, state: dict[str, Any]) -> None:
    existing = (
        db.query(Checkpoint).filter(Checkpoint.run_id == run.id, Checkpoint.stage == stage).one_or_none()
    )
    blob = json.dumps(state)
    if existing:
        existing.payload_json = blob
        existing.finished_at = utcnow()
    else:
        db.add(Checkpoint(run_id=run.id, stage=stage, payload_json=blob))
    run.current_stage = stage
    run.state_json = blob
    run.updated_at = utcnow()
    db.commit()


def finished_stages(db: Session, run_id: str) -> set[str]:
    rows = db.query(Checkpoint).filter(Checkpoint.run_id == run_id).all()
    return {r.stage for r in rows}


def add_cost(db: Session, run: Run, stage: str, ms: float, llm: Any | None = None) -> None:
    db.add(
        StageCost(
            run_id=run.id,
            stage=stage,
            ms=ms,
            llm_calls=getattr(llm, "calls", 0) or 0,
            prompt_tokens=getattr(llm, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(llm, "completion_tokens", 0) or 0,
            usd=getattr(llm, "usd", 0) or 0,
        )
    )
    db.commit()


def load_state(run: Run) -> dict[str, Any]:
    return json.loads(run.state_json or "{}")


def _doc_dicts(docs: list[Document]) -> list[dict[str, Any]]:
    return [
        {"id": d.id, "filename": d.filename, "text": d.text, "kind": d.kind, "sha256": d.sha256}
        for d in docs
    ]


class Engine:
    def __init__(self, db: Session, llm=None):
        self.db = db
        self.llm = llm or get_llm()

    def ingest_file(self, pile: Pile, filename: str, data: bytes) -> Document:
        digest = sha256_bytes(data)
        existing = (
            self.db.query(Document)
            .filter(Document.pile_id == pile.id, Document.sha256 == digest)
            .one_or_none()
        )
        if existing:
            return existing
        text = parse_bytes(filename, data)
        doc = Document(pile_id=pile.id, filename=filename, sha256=digest, text=text)
        self.db.add(doc)
        self.db.flush()
        for i, chunk in enumerate(_chunk_text(text)):
            self.db.add(
                Chunk(
                    document_id=doc.id,
                    pile_id=pile.id,
                    ordinal=i,
                    text=chunk,
                    embedding=dump_vec(hash_embedding(chunk)),
                )
            )
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def seed_pile(self, name: str, folder: Path) -> Pile:
        pile = Pile(name=name)
        self.db.add(pile)
        self.db.commit()
        for path in sorted(folder.iterdir()):
            if path.is_file():
                self.ingest_file(pile, path.name, path.read_bytes())
        return pile

    def start_run(
        self,
        pile: Pile,
        playbook: dict[str, Any] | None = None,
        trigger: str = "full",
        new_document_ids: list[str] | None = None,
    ) -> Run:
        run = Run(
            pile_id=pile.id,
            status="running",
            current_stage="ingest",
            trigger=trigger,
            new_document_ids=json.dumps(new_document_ids or []),
            playbook_json=json.dumps(playbook or {"rules": []}),
            state_json="{}",
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def kill(self, run: Run) -> None:
        run.kill_requested = 1
        self.db.commit()

    def advance(
        self,
        run: Run,
        stop_after: str | None = None,
        pause: Callable[[], None] | None = None,
        pause_on_stop: bool = True,
    ) -> Run:
        """Run remaining stages until gate, kill, or stop_after (for tests / graph steps)."""
        with lock_for(run.pile_id):
            return self._advance_locked(run, stop_after, pause, pause_on_stop)

    def _advance_locked(
        self,
        run: Run,
        stop_after: str | None,
        pause: Callable[[], None] | None,
        pause_on_stop: bool,
    ) -> Run:
        done = finished_stages(self.db, run.id)
        state = load_state(run)
        for stage in STAGES:
            self.db.refresh(run)
            if stage in done:
                continue
            if run.kill_requested:
                run.status = "paused"
                self.db.commit()
                log_event(self.db, run, stage, "killed before stage; finished work kept")
                return run
            if state.get("empty_pile") and stage not in ("ingest", "gate"):
                state.setdefault("path_decisions", []).append(
                    {
                        "stage": stage,
                        "decision": "skip",
                        "reason": "empty pile; nothing to extract until a human adds sources",
                    }
                )
                save_checkpoint(self.db, run, stage, state)
                log_event(self.db, run, stage, f"skipped {stage} (empty pile)")
                if stop_after == stage:
                    if pause_on_stop:
                        run.status = "paused"
                        self.db.commit()
                    return run
                continue
            if pause:
                pause()
            t0 = time.perf_counter()
            state, llm_usage = self._run_stage(run, stage, state)
            add_cost(self.db, run, stage, (time.perf_counter() - t0) * 1000, llm_usage)
            save_checkpoint(self.db, run, stage, state)
            log_event(self.db, run, stage, f"finished {stage}")
            if stage == "gate":
                run.status = "awaiting_review"
                self.db.commit()
                return run
            if stop_after == stage:
                if pause_on_stop:
                    run.status = "paused"
                    self.db.commit()
                return run
        return run

    def _run_stage(self, run: Run, stage: str, state: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        docs = self.db.query(Document).filter(Document.pile_id == run.pile_id).all()
        usage = None
        if stage == "ingest":
            state["documents"] = _doc_dicts(docs)
            state["path_decisions"] = [{"stage": "ingest", "decision": "continue", "reason": f"{len(docs)} files"}]
            if not docs:
                state["empty_pile"] = True
                state["path_decisions"].append(
                    {"stage": "ingest", "decision": "escalate", "reason": "empty pile; human must add sources"}
                )
            return state, usage

        if stage == "classify":
            classified = []
            decisions = list(state.get("path_decisions") or [])
            for d in docs:
                result = self.llm.complete("classify", {"text": d.text, "filename": d.filename})
                usage = _merge_usage(usage, result)
                parsed = json.loads(result.text)
                d.kind = parsed.get("kind") or "unknown"
                classified.append({**_doc_dicts([d])[0], **parsed})
                decisions.append(
                    {
                        "stage": "classify",
                        "document": d.filename,
                        "decision": parsed.get("decision", "continue"),
                        "reason": parsed.get("reason", ""),
                        "kind": d.kind,
                    }
                )
                if parsed.get("decision") == "skip":
                    log_event(self.db, run, "classify", f"skipped {d.filename}")
            self.db.commit()
            state["documents"] = classified
            state["path_decisions"] = decisions
            return state, usage

        if stage == "extract":
            facts: list[dict[str, Any]] = []
            retries = 0
            for d in state.get("documents") or _doc_dicts(docs):
                if d.get("decision") == "skip" or d.get("kind") == "unreadable":
                    continue
                attempt = 0
                while attempt < 2:
                    result = self.llm.complete("extract", {"text": d.get("text", ""), "filename": d.get("filename")})
                    usage = _merge_usage(usage, result)
                    parsed = json.loads(result.text)
                    extracted = parsed.get("facts") or []
                    if extracted or d.get("kind") == "injection_attempt":
                        for f in extracted:
                            f["document_id"] = d["id"]
                            f["filename"] = d.get("filename")
                            facts.append(f)
                        break
                    attempt += 1
                    retries += 1
                    state.setdefault("path_decisions", []).append(
                        {
                            "stage": "extract",
                            "decision": "retry",
                            "reason": f"no facts from {d.get('filename')}; retry {attempt}",
                        }
                    )
            state["facts"] = facts
            if retries:
                log_event(self.db, run, "extract", f"retried {retries} time(s)")
            return state, usage

        if stage == "reconcile":
            conflicts = _find_conflicts(state.get("facts") or [])
            state["conflicts"] = conflicts
            if conflicts:
                state.setdefault("path_decisions", []).append(
                    {
                        "stage": "reconcile",
                        "decision": "escalate",
                        "reason": f"{len(conflicts)} unresolved disagreements",
                    }
                )
            return state, usage

        if stage == "draft":
            new_ids = json.loads(run.new_document_ids or "[]")
            prior = _prior_register(self.db, run)
            sections = _draft_register(state.get("facts") or [], state.get("conflicts") or [], state.get("documents") or [])
            if run.trigger == "incremental" and prior and new_ids:
                sections = _patch_register(prior, sections, new_ids)
            state["register"] = sections
            return state, usage

        if stage == "examine":
            playbook = load_playbook(run.playbook_json)
            findings = evaluate_playbook(state.get("facts") or [], state.get("documents") or [], playbook)
            # injection is always a finding, never a command
            for d in state.get("documents") or []:
                if d.get("kind") == "injection_attempt" or looks_like_injection(d.get("text") or ""):
                    findings.append(
                        {
                            "rule_id": "prompt-injection",
                            "title": "Source tried to give the system orders",
                            "severity": "high",
                            "body": "The document contains instruction-like text aimed at the agent. It was treated as data, not executed.",
                            "locator": d.get("filename"),
                            "quote": "ignore previous instructions",
                        }
                    )
            state["findings"] = findings
            return state, usage

        if stage == "gate":
            self._materialize_review(run, state)
            return state, usage

        raise RuntimeError(stage)

    def _materialize_review(self, run: Run, state: dict[str, Any]) -> None:
        self.db.query(ReviewItem).filter(ReviewItem.run_id == run.id).delete()
        items: list[ReviewItem] = []
        for c in state.get("conflicts") or []:
            items.append(
                ReviewItem(
                    run_id=run.id,
                    item_type="conflict",
                    title=c.get("title") or c.get("key"),
                    body=c.get("body", ""),
                    locator=c.get("locator", ""),
                    payload_json=json.dumps(c),
                )
            )
        for f in state.get("findings") or []:
            items.append(
                ReviewItem(
                    run_id=run.id,
                    item_type="finding",
                    title=f.get("title") or f.get("rule_id"),
                    body=f.get("body", ""),
                    locator=str(f.get("locator") or ""),
                    payload_json=json.dumps(f),
                )
            )
        for sec in state.get("register") or []:
            if sec.get("unchanged"):
                continue
            if run.trigger == "incremental":
                items.append(
                    ReviewItem(
                        run_id=run.id,
                        item_type="update",
                        title=f"Update section: {sec['title']}",
                        body=sec["body"],
                        locator=",".join(sec.get("source_ids") or []),
                        payload_json=json.dumps(sec),
                    )
                )
        if run.trigger != "incremental":
            items.append(
                ReviewItem(
                    run_id=run.id,
                    item_type="update",
                    title="Commit grounded register",
                    body="Approve to commit the register built from cited facts. Reject to leave the pile unchanged.",
                    locator="",
                    payload_json=json.dumps({"kind": "commit_register"}),
                )
            )
        self.db.add_all(items)
        self.db.commit()

    def apply_review(self, run: Run, decisions: dict[str, str]) -> Run:
        """decisions maps review item id -> approved|rejected. Unmentioned stay pending."""
        items = self.db.query(ReviewItem).filter(ReviewItem.run_id == run.id).all()
        pending = [i for i in items if i.status == "pending"]
        for item in pending:
            choice = decisions.get(item.id)
            if choice in ("approved", "rejected"):
                item.status = choice
        self.db.commit()
        still = self.db.query(ReviewItem).filter(ReviewItem.run_id == run.id, ReviewItem.status == "pending").count()
        if still:
            run.status = "awaiting_review"
            self.db.commit()
            return run
        approved_register = any(
            i.item_type == "update" and i.status == "approved" for i in self.db.query(ReviewItem).filter(ReviewItem.run_id == run.id)
        )
        if not approved_register and run.trigger != "incremental":
            run.status = "rejected"
            run.error = "human rejected the register commit"
            self.db.commit()
            return run
        self._commit_register(run)
        return run

    def _commit_register(self, run: Run) -> None:
        state = load_state(run)
        self.db.query(RegisterSection).filter(RegisterSection.run_id == run.id).delete()
        rejected_update_ids = {
            json.loads(i.payload_json).get("section_id")
            for i in self.db.query(ReviewItem).filter(ReviewItem.run_id == run.id, ReviewItem.item_type == "update", ReviewItem.status == "rejected")
        }
        for sec in state.get("register") or []:
            if sec.get("section_id") in rejected_update_ids:
                continue
            self.db.add(
                RegisterSection(
                    run_id=run.id,
                    section_id=sec["section_id"],
                    title=sec["title"],
                    body=sec["body"],
                    source_ids=json.dumps(sec.get("source_ids") or []),
                    content_hash=sec["content_hash"],
                    unchanged=1 if sec.get("unchanged") else 0,
                )
            )
        pile = self.db.get(Pile, run.pile_id)
        if pile:
            pile.version += 1
        run.status = "committed"
        self.db.commit()


def _merge_usage(a, b):
    if a is None:
        return b
    a.calls = getattr(a, "calls", 0) + getattr(b, "calls", 0)
    a.prompt_tokens = getattr(a, "prompt_tokens", 0) + getattr(b, "prompt_tokens", 0)
    a.completion_tokens = getattr(a, "completion_tokens", 0) + getattr(b, "completion_tokens", 0)
    a.usd = getattr(a, "usd", 0) + getattr(b, "usd", 0)
    return a


def _chunk_text(text: str, size: int = 800) -> list[str]:
    if not text.strip():
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def _find_conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same key, different values, from different documents — never auto-resolved."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        if f.get("key") in ("quote", "injection_attempt"):
            continue
        groups.setdefault(f["key"], []).append(f)
    conflicts = []
    for key, items in groups.items():
        values = {str(i["value"]).strip().lower() for i in items}
        docs = {i.get("document_id") for i in items}
        if len(values) > 1 and len(docs) > 1:
            # temporal keys: keep latest by document kind later; still surface if invoice vs contract
            body = "; ".join(f"{i.get('filename')}: {i['value']}" for i in items)
            conflicts.append(
                {
                    "id": new_id(),
                    "key": key,
                    "title": f"Disagreement on {key}",
                    "body": body,
                    "locator": " | ".join(i.get("locator") or "" for i in items),
                    "values": [i["value"] for i in items],
                    "source_ids": [i.get("document_id") for i in items],
                }
            )
    return conflicts


def _draft_register(facts: list[dict[str, Any]], conflicts: list[dict[str, Any]], documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def collect(key: str) -> list[dict[str, Any]]:
        return [f for f in facts if f.get("key") == key]

    def cited_line(title: str, key: str) -> tuple[str, list[str]]:
        items = collect(key)
        if not items:
            return f"{title}: not supported by the sources.", []
        commercial = key in ("annual_fee_usd", "quarterly_fee_usd", "monthly_fee_usd", "net_days", "uptime", "autorenew_notice_days")

        def rank(i: dict[str, Any]) -> int:
            fn = (i.get("filename") or "").lower()
            if commercial and "amend" in fn:
                return 0
            if "msa" in fn or "agreement" in fn:
                return 1
            if "amend" in fn:
                return 2
            if "inv" in fn:
                return 4
            return 3

        picked = sorted(items, key=rank)[0]
        src = [picked.get("document_id")]
        line = f"{title}: {picked['value']} (source: {picked.get('locator')})"
        return line, [s for s in src if s]

    sections_spec = [
        ("parties", "Parties", ["buyer", "vendor"]),
        ("commercials", "Fees and invoices", ["annual_fee_usd", "quarterly_fee_usd", "monthly_fee_usd", "invoice_amount_usd"]),
        ("payment", "Payment terms", ["net_days"]),
        ("term", "Term and renewal", ["effective_date", "autorenew_notice_days"]),
        ("sla", "Service levels", ["uptime"]),
    ]
    out = []
    for sid, title, keys in sections_spec:
        lines = []
        sources: list[str] = []
        for key in keys:
            line, src = cited_line(key.replace("_", " "), key)
            lines.append(line)
            sources.extend(src)
        body = "\n".join(lines)
        out.append(
            {
                "section_id": sid,
                "title": title,
                "body": body,
                "source_ids": list(dict.fromkeys(sources)),
                "content_hash": _hash_text(body),
                "unchanged": False,
            }
        )
    conflict_body = "No disagreements among sources." if not conflicts else "\n".join(c["body"] for c in conflicts)
    out.append(
        {
            "section_id": "conflicts",
            "title": "Open disagreements",
            "body": conflict_body,
            "source_ids": [],
            "content_hash": _hash_text(conflict_body),
            "unchanged": False,
        }
    )
    kinds = [d.get("kind") for d in documents]
    inventory = "Documents in pile:\n" + "\n".join(f"- {d.get('filename')} ({d.get('kind')})" for d in documents)
    out.append(
        {
            "section_id": "inventory",
            "title": "Source inventory",
            "body": inventory,
            "source_ids": [d["id"] for d in documents],
            "content_hash": _hash_text(inventory),
            "unchanged": False,
        }
    )
    _ = kinds
    return out


def _prior_register(db: Session, run: Run) -> list[dict[str, Any]] | None:
    prior = (
        db.query(Run)
        .filter(Run.pile_id == run.pile_id, Run.status == "committed", Run.id != run.id)
        .order_by(Run.updated_at.desc())
        .first()
    )
    if not prior:
        return None
    rows = db.query(RegisterSection).filter(RegisterSection.run_id == prior.id).all()
    return [
        {
            "section_id": r.section_id,
            "title": r.title,
            "body": r.body,
            "source_ids": json.loads(r.source_ids or "[]"),
            "content_hash": r.content_hash,
            "unchanged": True,
        }
        for r in rows
    ]


def _patch_register(
    prior: list[dict[str, Any]], fresh: list[dict[str, Any]], new_document_ids: list[str]
) -> list[dict[str, Any]]:
    new_set = set(new_document_ids)
    fresh_by = {s["section_id"]: s for s in fresh}
    out = []
    for old in prior:
        incoming = fresh_by.get(old["section_id"])
        if not incoming:
            out.append(old)
            continue
        touches = set(incoming.get("source_ids") or []) & new_set
        # inventory always updates; conflicts update if any new facts
        if old["section_id"] in ("inventory", "conflicts") or touches:
            incoming["unchanged"] = incoming["content_hash"] == old["content_hash"]
            if incoming["unchanged"]:
                incoming["body"] = old["body"]
            out.append(incoming)
        else:
            # prove byte-identity
            cloned = dict(old)
            cloned["unchanged"] = True
            cloned["content_hash"] = old["content_hash"]
            cloned["body"] = old["body"]
            out.append(cloned)
    return out
