# Vendor Analyst

An agentic system that owns a pile of vendor contracts, amendments, and invoices: extract cited facts, surface disagreements, check a playbook, and apply only the updates a human (or another program) approved.

This is Task 1 of an engineering round. The system is independent of SuperDocs.

## What it accepts

- **Domain:** vendor MSA / SOW, numbered amendments, invoices, plus injection-attempt memos treated as data.
- **Formats:** `.txt`, `.md`, `.pdf`, `.docx`.
- **Playbook:** JSON rules (see `fixtures/playbooks/vendor-compliance.json`). A new client or cap is a playbook change, not a rewrite.

A second run means a different pile inside that set, not a different product. Fixtures include `acme-vendor` (conflicts + injection) and `clean-vendor` (honest empty findings).

## One command (stranger path)

```bash
docker compose up --build
```

Then open http://localhost:8080

The API is http://localhost:8000/docs

Seed the demo pile:

```bash
curl -X POST http://localhost:8000/piles -H "Content-Type: application/json" -d "{\"name\":\"acme-vendor\",\"seed\":\"acme\"}"
```

Or upload files onto a pile, then `POST /piles/{id}/runs`. Drop later files into `data/watch/<pile_id>/` and click **Watch folder**, or wait for the poller.

## Tests (no live key)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -q
```

Tests cover kill/resume, concurrent piles, the same pile hit twice, document-borne prompt injection, mixed approve/reject, empty-pile skip/escalate, and unchanged-section hashes on incremental updates. Default LLM is a deterministic fake provider.

## Machine interface

REST is the full flow. MCP stdio mirrors it, including explicit approve/reject:

```bash
cd backend
python -m app.mcp_server
```

Tools: `create_pile`, `start_run`, `ingest_document`, `get_status`, `get_pending_review`, `approve_items`, `reject_items`, `get_register`, `get_cost`.

Approval is always an explicit operation. Nothing commits because a model felt done. `GET /runs/{id}/register` returns 409 until status is `committed`.

## Kill and resume

`POST /runs/{id}/kill` stops after the current stage boundary. `POST /runs/{id}/resume` continues from the last finished checkpoint. Finished artifacts stay.

## Architecture (short)

LangGraph owns the path: ingest → classify → extract → reconcile → draft → examine → gate → commit.

Each graph node runs one checkpointed engine stage on the request's database session. Path decisions are real: retry extract when a file yields no facts, skip unreadable files, skip the middle of an empty pile and escalate to a person, escalate conflicts and injection attempts. Kill routes the graph to stop.

PostgreSQL + pgvector is the Docker path. Tests use SQLite and hash embeddings so pytest needs no Postgres and no API key.

## What we cut

No OCR of scanned image-only PDFs. Watcher is a directory poll under `data/watch/<pile_id>`, not a proprietary DMS. Those cuts are defended in PROGRESS.md.

## Private GitHub

Repository name: `doctask-kaveri-mekala` (no SuperDocs in the name). Invite `o-kadam`.
