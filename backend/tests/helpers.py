from app.engine import Engine
from app.models import ReviewItem


def seed(eng: Engine, name: str):
    from app.paths import fixtures_dir

    folder = fixtures_dir() / "piles" / name
    return eng.seed_pile(name, folder)


def decide(db, run, mixed_reject_first_finding: bool = False, reject_updates: bool = False):
    items = db.query(ReviewItem).filter(ReviewItem.run_id == run.id).all()
    decisions = {}
    finding_seen = False
    for item in items:
        if mixed_reject_first_finding and item.item_type == "finding" and not finding_seen:
            decisions[item.id] = "rejected"
            finding_seen = True
        elif reject_updates and item.item_type == "update":
            decisions[item.id] = "rejected"
        else:
            decisions[item.id] = "approved"
    return Engine(db).apply_review(run, decisions)
