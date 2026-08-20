from __future__ import annotations

import json
from typing import Any


def load_playbook(raw: str | dict | None) -> dict[str, Any]:
    if not raw:
        return {"rules": []}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def evaluate_playbook(facts: list[dict[str, Any]], documents: list[dict[str, Any]], playbook: dict[str, Any]) -> list[dict[str, Any]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        by_key.setdefault(f["key"], []).append(f)

    def latest(key: str) -> dict[str, Any] | None:
        items = by_key.get(key) or []
        return items[-1] if items else None

    findings: list[dict[str, Any]] = []
    kinds = {d.get("kind") for d in documents}

    for rule in playbook.get("rules", []):
        kind = rule.get("kind")
        rid = rule["id"]
        title = rule["title"]
        severity = rule.get("severity", "medium")

        if kind == "max_net_days":
            cap = int(rule.get("max", 30))
            for f in by_key.get("net_days", []):
                try:
                    days = int(float(f["value"]))
                except ValueError:
                    continue
                if days > cap:
                    # amendment that only restates Net 30 should not fire; invoice over cap should
                    findings.append(
                        {
                            "rule_id": rid,
                            "title": title,
                            "severity": severity,
                            "body": f"Payment terms Net {days} exceed the cap of Net {cap}.",
                            "locator": f.get("locator", ""),
                            "quote": f.get("quote", ""),
                        }
                    )

        elif kind == "invoice_matches_fee":
            quarterly = latest("quarterly_fee_usd")
            monthly = latest("monthly_fee_usd")
            for inv in by_key.get("invoice_amount_usd", []):
                expected = None
                label = ""
                if quarterly:
                    expected = quarterly["value"]
                    label = "quarterly fee"
                elif monthly:
                    expected = monthly["value"]
                    label = "monthly fee"
                if expected is not None and str(inv["value"]) != str(expected):
                    findings.append(
                        {
                            "rule_id": rid,
                            "title": title,
                            "severity": severity,
                            "body": f"Invoice amount USD {inv['value']} does not match contracted {label} USD {expected}.",
                            "locator": inv.get("locator", ""),
                            "quote": inv.get("quote", ""),
                        }
                    )

        elif kind == "named_parties":
            if not latest("buyer") or not latest("vendor"):
                findings.append(
                    {
                        "rule_id": rid,
                        "title": title,
                        "severity": severity,
                        "body": "Buyer or vendor is missing from extracted facts.",
                        "locator": "",
                        "quote": "",
                    }
                )

        elif kind == "amendment_cites_section":
            if "amendment" in kinds:
                if not by_key.get("amends_section"):
                    findings.append(
                        {
                            "rule_id": rid,
                            "title": title,
                            "severity": severity,
                            "body": "Amendment does not cite the MSA section it changes.",
                            "locator": "",
                            "quote": "",
                        }
                    )

        elif kind == "autorenew_notice":
            # only if auto-renew language exists without notice days
            pass

    # auto-renew: if MSA text implied autorenew we already extract notice days; if autorenew words exist without days, finding
    for d in documents:
        text = (d.get("text") or "").lower()
        if "no auto-renew" in text or "no auto renewal" in text:
            continue
        if "auto-renew" in text or "auto_renew" in text or "auto renews" in text:
            if not by_key.get("autorenew_notice_days"):
                findings.append(
                    {
                        "rule_id": "no-auto-renew-silence",
                        "title": "Auto-renewal, if present, must state notice period in days",
                        "severity": "low",
                        "body": "Auto-renewal language found without a parsed notice period.",
                        "locator": d.get("filename", ""),
                        "quote": "auto-renew",
                    }
                )

    return findings
