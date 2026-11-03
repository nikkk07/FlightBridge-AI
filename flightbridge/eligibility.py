"""Per-country eligibility: is this a school we may partner with, by that country's own standards?

  eligible_verified  an official register lists the credential, or a person confirmed it on the regulator's site
  eligible_claimed   the school's own website states the credential; a person must confirm before outreach
  unqualified        the website was read and states no credential, or it is helicopter/drone-only, or closed
  unknown            not researched yet, or the country has no rules configured

Rules live in config/eligibility.json. Every decision stores its reasons with the evidence (register entry or
the exact website excerpt), so the "why" is always visible. Only eligible_verified schools are drafted for outreach.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import db

RULES_PATH = Path(__file__).resolve().parent / "config" / "eligibility.json"
CLAIM_TEXT = {
    "part141": "Website says FAA Part 141",
    "sevp": "Website says SEVP / I-20 / M-1 (international students)",
    "casa_part14x": "Website says CASA Part 141/142",
    "cricos_on_site": "Website shows a CRICOS registration",
    "transport_canada": "Website says Transport Canada approved / Flight Training Unit",
    "dli": "Website shows a Designated Learning Institution (DLI) number",
    "caa_nz_part141": "Website says CAA NZ Part 141",
    "nzqa": "Website says NZQA",
}


def load_rules() -> dict:
    return {k: v for k, v in json.loads(RULES_PATH.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def evaluate(country: str, regulatory: list[dict], profile: dict | None, pages_read: int, business_closed: bool = False,
             rules: dict | None = None) -> tuple[str, list[dict]]:
    """Pure decision function (no DB). regulatory: [{classification, status, authority_code, approval_ref, source_url}]."""
    rules = rules if rules is not None else load_rules()
    rule = rules.get(country)
    if not rule:
        return "unknown", [{"code": "no_rules", "text": f"No eligibility rules set for {country} yet"}]
    reasons: list[dict] = []
    if business_closed:
        return "unqualified", [{"code": "closed", "text": "Listed as permanently closed"}]
    for r in regulatory:
        if r["classification"] in rule["verified"] and r["status"] in ("listed_in_register", "verified_by_human"):
            how = "Confirmed by a person on the regulator's site" if r["status"] == "verified_by_human" else "Listed in the official register"
            reasons.append({"code": "verified", "text": f"{how}: {r['authority_code']} {r.get('approval_ref') or ''}".strip(),
                            "source_url": r.get("source_url")})
    signals = (profile or {}).get("signals") or {}
    evidence = {e["signal"]: e for e in (profile or {}).get("evidence") or []}
    if reasons:
        return "eligible_verified", reasons
    claims = [c for c in rule["claims"] if signals.get(c)]
    if signals.get("helicopter") and not signals.get("aeroplane") and not signals.get("has_cpl"):
        return "unqualified", [{"code": "helicopter_only", "text": "Website mentions helicopters only (no aeroplane training)"}]
    if signals.get("drone") and not signals.get("aeroplane") and not signals.get("has_cpl") and not claims:
        return "unqualified", [{"code": "drone_only", "text": "Website is about drones, not pilot training"}]
    if claims:
        for c in claims:
            ev = evidence.get(c) or {}
            reasons.append({"code": f"claim:{c}", "text": CLAIM_TEXT.get(c, c), "excerpt": ev.get("excerpt"), "source_url": ev.get("url")})
        return "eligible_claimed", reasons
    if pages_read > 0:
        return "unqualified", [{"code": "no_credential", "text": f"Website read ({pages_read} pages): no {rule['credential']} found"}]
    return "unknown", [{"code": "not_researched", "text": "Website not read yet"}]


def evaluate_org(conn, org_id) -> str:
    row = conn.execute("SELECT country, org_kind FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
    if not row:
        return "unknown"
    country, kind = row
    if kind == "ground_school":         # demand-hub institutes are not flying schools: not assessed
        return "unknown"
    cur = conn.execute("SELECT eligibility, eligibility_reasons FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
    if cur[0] == "unqualified" and any(r.get("code") == "human_unqualified" for r in (cur[1] or [])):
        return "unqualified"            # a person's decision is never overwritten by automation
    reg = [dict(zip(("classification", "status", "authority_code", "approval_ref", "source_url"), r)) for r in conn.execute(
        """SELECT classification, status, authority_code, approval_ref, source_url FROM flightbridge.regulatory_approval
           WHERE org_id=%s""", (org_id,)).fetchall()]
    prof = conn.execute("SELECT signals, evidence FROM flightbridge.organisation_profile WHERE org_id=%s", (org_id,)).fetchone()
    pages = conn.execute("SELECT count(*) FROM flightbridge.website_page WHERE org_id=%s AND http_status=200", (org_id,)).fetchone()[0]
    closed = conn.execute("""SELECT 1 FROM flightbridge.audit_log WHERE entity='organisation' AND entity_id=%s
                             AND action='place_closed' LIMIT 1""", (str(org_id),)).fetchone() is not None
    decision, reasons = evaluate(country, reg, {"signals": prof[0], "evidence": prof[1]} if prof else None, pages, closed)
    old = conn.execute("SELECT eligibility FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()[0]
    conn.execute("""UPDATE flightbridge.organisation SET eligibility=%s, eligibility_reasons=%s, eligibility_checked_at=now()
                    WHERE id=%s""", (decision, json.dumps(reasons), org_id))
    if old != decision:
        db.audit(conn, "eligibility_set", "organisation", org_id, {"from": old, "to": decision, "reasons": reasons})
    return decision


def mark_unqualified(conn, org_id, employee_id, note: str) -> None:
    reasons = [{"code": "human_unqualified", "text": f"Marked unqualified by a person: {note}"}]
    conn.execute("""UPDATE flightbridge.organisation SET eligibility='unqualified', eligibility_reasons=%s,
                    eligibility_checked_at=now() WHERE id=%s""", (json.dumps(reasons), org_id))
    db.audit(conn, "eligibility_set", "organisation", org_id, {"to": "unqualified", "by": "employee", "note": note},
             actor_type="employee", actor_id=employee_id)


def confirm(conn, org_id, employee_id, reference: str | None, source_url: str | None) -> str:
    """A person checked the regulator's site: record the country's main credential as verified_by_human."""
    country = conn.execute("SELECT country FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()[0]
    rule = load_rules().get(country)
    if not rule:
        raise ValueError(f"No eligibility rules for {country}")
    c = rule["confirm_as"]
    conn.execute("""INSERT INTO flightbridge.regulatory_approval (org_id, authority_code, classification, approval_ref, status,
                       source_url, checked_at, checked_by, next_check_due)
                    VALUES (%s,%s,%s,%s,'verified_by_human',%s,now(),%s,now() + interval '90 days')
                    ON CONFLICT (org_id, authority_code, classification) DO UPDATE
                      SET status='verified_by_human', approval_ref=coalesce(EXCLUDED.approval_ref, flightbridge.regulatory_approval.approval_ref),
                          source_url=EXCLUDED.source_url, checked_at=now(), checked_by=EXCLUDED.checked_by,
                          next_check_due=EXCLUDED.next_check_due""",
                 (org_id, c["authority"], c["classification"], reference or None, source_url or rule["lookup"][0][1], employee_id))
    conn.execute("UPDATE flightbridge.organisation SET eligibility_reasons='[]'::jsonb WHERE id=%s", (org_id,))  # clear human_unqualified
    return evaluate_org(conn, org_id)


def run(database_url: str, limit: int, org_id: str | None = None) -> dict:
    """Re-evaluate schools whose evidence changed since the last check (or never checked)."""
    with db.tx(database_url) as conn:
        if org_id:
            ids = [org_id]
        else:
            ids = [r[0] for r in conn.execute("""
                SELECT o.id FROM flightbridge.organisation o
                 WHERE o.deleted_at IS NULL AND o.org_kind <> 'ground_school'
                   AND NOT (o.eligibility_reasons @> '[{"code": "human_unqualified"}]'::jsonb)
                   AND (o.eligibility_checked_at IS NULL
                        OR o.eligibility_checked_at < greatest(
                             (SELECT max(checked_at) FROM flightbridge.regulatory_approval r WHERE r.org_id=o.id),
                             (SELECT updated_at FROM flightbridge.organisation_profile p WHERE p.org_id=o.id)))
                 ORDER BY o.eligibility_checked_at NULLS FIRST LIMIT %s""", (limit,)).fetchall()]
        out = {"checked": 0, "eligible_verified": 0, "eligible_claimed": 0, "unqualified": 0, "unknown": 0}
        for oid in ids:
            out[evaluate_org(conn, oid)] += 1
            out["checked"] += 1
    return out
