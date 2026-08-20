# Assumptions and calls

- 2026-08-20: Domain is synthetic vendor contracting (MSA + amendment + invoices). Real confidential files are out of scope.
- 2026-08-20: Default LLM is a deterministic fake provider so clone-to-run and pytest never need a live key. Real OpenAI/Anthropic is optional.
- 2026-08-20: "Watched location" is a filesystem directory plus `POST /piles/{id}/documents`. Compose mounts `./data/watch`.
- 2026-08-20: Incremental updates patch register sections whose `source_ids` intersect the new document; other sections keep the same bytes/hash.
- 2026-08-20: Conflicts are never auto-resolved; they become review items.
- 2026-08-20: Vector search uses pgvector in Docker; tests use a hash embedding + in-process cosine so pytest needs no Postgres.
- 2026-08-20: MCP mirrors REST: create/start/ingest/status/pending/approve/reject/register/cost.
- 2026-08-20: LangGraph nodes execute engine stages on the request session (contextvar). Labels without work would fail the brief; `graph.invoke` on a second engine was the bug that hid that.
- 2026-08-20: Empty piles skip extract/draft and escalate. An update that does not touch a section keeps the prior bytes and SHA-256.
- 2026-08-20: Watch poller is opt-out (`WATCH_ENABLED=0` in tests). Only hashes not already in the pile start an incremental run.
