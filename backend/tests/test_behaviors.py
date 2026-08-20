from app.engine import Engine, finished_stages
from app.models import RegisterSection, ReviewItem, Run

from tests.helpers import decide, seed


def test_kill_and_resume_keeps_finished_work(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "acme-vendor")
    run = eng.start_run(pile, playbook)
    eng.advance(run, stop_after="extract")
    db.refresh(run)
    assert run.status == "paused"
    done = finished_stages(db, run.id)
    assert "extract" in done
    assert "gate" not in done
    facts = (run.state_json or "")
    assert "annual_fee_usd" in facts

    eng.advance(run)
    db.refresh(run)
    assert run.status == "awaiting_review"
    assert "gate" in finished_stages(db, run.id)
    assert "annual_fee_usd" in run.state_json


def test_same_pile_hit_twice_does_not_corrupt(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "clean-vendor")
    r1 = eng.start_run(pile, playbook)
    eng.advance(r1)
    decide(db, r1)
    db.refresh(r1)
    assert r1.status == "committed"
    r2 = eng.start_run(pile, playbook)
    eng.advance(r2)
    decide(db, r2)
    db.refresh(r2)
    assert r2.status == "committed"
    assert r1.id != r2.id
    s1 = {s.section_id: s.content_hash for s in db.query(RegisterSection).filter_by(run_id=r1.id)}
    s2 = {s.section_id: s.content_hash for s in db.query(RegisterSection).filter_by(run_id=r2.id)}
    assert s1["parties"] == s2["parties"]


def test_document_orders_are_not_followed(db, playbook):
    import json

    eng = Engine(db)
    pile = seed(eng, "acme-vendor")
    run = eng.start_run(pile, playbook)
    eng.advance(run)
    state = json.loads(run.state_json)
    fees = [f["value"] for f in state.get("facts") or [] if f.get("key") == "annual_fee_usd"]
    assert "1" not in fees
    items = db.query(ReviewItem).filter_by(run_id=run.id, item_type="finding").all()
    assert any("orders" in i.body.lower() or "instruction" in i.body.lower() for i in items)
    assert any(d.get("kind") == "injection_attempt" for d in state.get("documents") or [])
    assert any(d.get("decision") == "escalate" for d in state.get("path_decisions") or [])


def test_mixed_approve_reject_does_not_drop_the_rest(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "acme-vendor")
    run = eng.start_run(pile, playbook)
    eng.advance(run)
    items = db.query(ReviewItem).filter_by(run_id=run.id).all()
    assert len(items) >= 2
    decide(db, run, mixed_reject_first_finding=True)
    db.refresh(run)
    assert run.status == "committed"
    statuses = {i.id: i.status for i in db.query(ReviewItem).filter_by(run_id=run.id)}
    assert "rejected" in statuses.values()
    assert "approved" in statuses.values()
    assert "pending" not in statuses.values()


def test_clean_corpus_can_have_no_findings(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "clean-vendor")
    run = eng.start_run(pile, playbook)
    eng.advance(run)
    findings = db.query(ReviewItem).filter_by(run_id=run.id, item_type="finding").all()
    assert findings == []


def test_unsupported_claim_is_said_so(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "clean-vendor")
    run = eng.start_run(pile, playbook)
    eng.advance(run)
    decide(db, run)
    sla = db.query(RegisterSection).filter_by(run_id=run.id, section_id="sla").one()
    # clean pile has uptime; commercials should not invent a quarterly fee
    commercials = db.query(RegisterSection).filter_by(run_id=run.id, section_id="commercials").one()
    assert "not supported by the sources" in commercials.body


def test_incremental_leaves_untouched_sections_byte_identical(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "clean-vendor")
    r1 = eng.start_run(pile, playbook)
    eng.advance(r1)
    decide(db, r1)
    parties1 = db.query(RegisterSection).filter_by(run_id=r1.id, section_id="parties").one()

    extra = (
        b"INVOICE\nDocument-ID: INV-CLEAN-FEB\nInvoice date: 2025-02-05\n"
        b"Buyer: Birchwood Labs LLC\nVendor: Pixel Grain Inc.\n"
        b"Period: 2025-02-01 to 2025-02-28\nAmount due: USD 4000\n"
        b"Payment terms stated on invoice: Net 15\nRelated contract: MSA-CLEAN-001\n"
    )
    doc = eng.ingest_file(pile, "INV-CLEAN-FEB.txt", extra)
    r2 = eng.start_run(pile, playbook, trigger="incremental", new_document_ids=[doc.id])
    eng.advance(r2)
    decide(db, r2)
    parties2 = db.query(RegisterSection).filter_by(run_id=r2.id, section_id="parties").one()
    assert parties2.unchanged == 1
    assert parties2.body == parties1.body
    assert parties2.content_hash == parties1.content_hash

    inventory2 = db.query(RegisterSection).filter_by(run_id=r2.id, section_id="inventory").one()
    assert "INV-CLEAN-FEB.txt" in inventory2.body


def test_success_is_not_claimed_before_commit(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "clean-vendor")
    run = eng.start_run(pile, playbook)
    eng.advance(run)
    assert run.status == "awaiting_review"
    assert db.query(RegisterSection).filter_by(run_id=run.id).count() == 0


def test_empty_pile_skips_and_escalates(db, playbook):
    from app.graph import invoke_run
    from app.models import Pile

    pile = Pile(name="empty")
    db.add(pile)
    db.commit()
    eng = Engine(db)
    run = eng.start_run(pile, playbook)
    invoke_run(db, run)
    db.refresh(run)
    assert run.status == "awaiting_review"
    import json

    state = json.loads(run.state_json)
    kinds = {d.get("decision") for d in state["path_decisions"]}
    assert "escalate" in kinds
    assert "skip" in kinds
    assert state.get("empty_pile") is True


def test_path_decisions_include_retry_or_escalate(db, playbook):
    eng = Engine(db)
    pile = seed(eng, "acme-vendor")
    run = eng.start_run(pile, playbook)
    eng.advance(run)
    import json

    decisions = json.loads(run.state_json)["path_decisions"]
    kinds = {d.get("decision") for d in decisions}
    assert "escalate" in kinds
