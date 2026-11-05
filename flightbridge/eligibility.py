"""Per-country eligibility: is this a school we may partner with, by that country's own standards?

  eligible_verified  an official register lists the credential, or a person confirmed it on the regulator's site
  eligible_claimed   "Check needed": the evidence conflicts or can't be confirmed automatically (a person decides)
  unqualified        not on the country's official lists (once they are all loaded), or the website was read and
                     states no credential, or it is helicopter/drone-only, or closed
  unknown            not decided yet (the Verify worker is still finding / reading the website)

Owner's rules (2026-11-05): decide Eligible or Not Qualified wherever the evidence allows; "Check needed" only when
  * the website claims a credential whose official list isn't loaded (e.g. SEVP before its list is uploaded), or
  * the website claims a credential but the loaded official list doesn't show the school (conflict), or
  * the school looks like a listed school under another name (same town, matching initials), or
  * no website could be found by every method (guess, Tavily, Google).
The AI may only REMOVE a website claim it can show is not about the credential (e.g. "I-20" the interstate), with a
verbatim quote; it never makes a school Eligible. Rules live in config/eligibility.json. Every decision stores its
reasons with the evidence (register entry or the exact website excerpt), so the "why" is always visible. Only
eligible_verified schools are drafted for outreach.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import db

RULES_PATH = Path(__file__).resolve().parent / "config" / "eligibility.json"
NATIONAL_APPROVAL = "NATIONAL_APPROVAL"   # a person's check where the country has no register yet
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


LIST_LABEL = {"faa_part141": "FAA Part 141 list", "sevp": "SEVP certified school list", "tc_ftu": "Transport Canada FTU list",
              "ircc_dli": "IRCC DLI list", "cricos": "CRICOS register", "casa_ato": "CASA flight training operators list"}


def _ai_claims(profile: dict | None) -> dict:
    ai = (profile or {}).get("ai") or {}
    return ai.get("claims") or {} if isinstance(ai, dict) else {}


def evaluate(country: str, regulatory: list[dict], profile: dict | None, pages_read: int, business_closed: bool = False,
             rules: dict | None = None, ctx: dict | None = None) -> tuple[str, list[dict]]:
    """Pure decision function (no DB). regulatory: [{classification, status, authority_code, approval_ref, source_url}].
    ctx: lists_loaded {list_kind: 'YYYY-MM-DD'}, site_exhausted (bool), lookalike {name, list, how} (weak match)."""
    rules = rules if rules is not None else load_rules()
    ctx = ctx or {}
    rule = rules.get(country)
    if not rule:
        return _no_rules(country, regulatory, profile, pages_read, business_closed, ctx)
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
    ai = _ai_claims(profile)
    claims = [c for c in rule["claims"] if signals.get(c) and ai.get(c, {}).get("verdict") != "no"]
    dropped = [c for c in rule["claims"] if signals.get(c) and c not in claims]
    if signals.get("helicopter") and not signals.get("aeroplane") and not signals.get("has_cpl"):
        return "unqualified", [{"code": "helicopter_only", "text": "Website mentions helicopters only (no aeroplane training)"}]
    if signals.get("drone") and not signals.get("aeroplane") and not signals.get("has_cpl") and not claims:
        return "unqualified", [{"code": "drone_only", "text": "Website is about drones, not pilot training"}]
    loaded = ctx.get("lists_loaded") or {}
    claim_lists = rule.get("claim_lists") or {}
    absent_when = rule.get("absent_when") or []
    look = ctx.get("lookalike")

    def claim_reasons(code_prefix: str = "claim:") -> list[dict]:
        out = []
        for c in claims:
            ev = evidence.get(c) or {}
            out.append({"code": f"{code_prefix}{c}", "text": CLAIM_TEXT.get(c, c), "excerpt": ev.get("excerpt"),
                        "source_url": ev.get("url")})
        return out

    def not_listed(kinds) -> str:
        return ", ".join(f"{LIST_LABEL.get(k, k)} ({loaded[k]})" for k in kinds if k in loaded)

    if look:      # same town, initials or name match a listed school: one click for a person to confirm
        return "eligible_claimed", [{"code": "possible_match", "text": f"Looks like {look['name']} on the "
                                     f"{LIST_LABEL.get(look['list'], look['list'])} ({look['how']}): confirm it is the same school",
                                     "source_url": look.get("source_url")}] + claim_reasons()
    if absent_when and all(k in loaded for k in absent_when):
        conflict = [c for c in claims if claim_lists.get(c) in absent_when]
        if conflict:
            return "eligible_claimed", [{"code": "claim_not_listed", "text": f"Website claims it, but the school is not on the "
                                         f"{not_listed(absent_when)}: check by hand"}] + claim_reasons()
        return "unqualified", [{"code": "not_listed", "text": f"Not on the {not_listed(absent_when)}"}]
    if claims:
        unverifiable = [c for c in claims if claim_lists.get(c) not in loaded]
        if unverifiable:
            return "eligible_claimed", claim_reasons()
        return "eligible_claimed", [{"code": "claim_not_listed", "text": "Website claims it, but the school is not on the "
                                     f"{not_listed({claim_lists[c] for c in claims})}: check by hand"}] + claim_reasons()
    if pages_read > 0:
        extra = f" (AI: {', '.join(CLAIM_TEXT.get(c, c).replace('Website says ', '') for c in dropped)} mention is not about "\
                "the credential)" if dropped else ""
        return "unqualified", [{"code": "no_credential", "text": f"Website read ({pages_read} pages): no {rule['credential']} found{extra}"}]
    if ctx.get("site_exhausted"):
        return "eligible_claimed", [{"code": "no_website", "text": "No website found (address guess, Tavily and Google search all "
                                     "tried): check the school by hand"}]
    return "unknown", [{"code": "not_researched", "text": "Website not read yet"}]


def _no_rules(country: str, regulatory: list[dict], profile: dict | None, pages_read: int,
              business_closed: bool, ctx: dict | None = None) -> tuple[str, list[dict]]:
    """Countries with no register set up yet (owner's decision, 2026-11-04): once the website has been read, the
    school goes to "Check needed" (eligible_claimed) for a person to confirm on the national aviation authority's
    site; that confirmation (NATIONAL_APPROVAL) makes it verified. Never drafted for outreach before that."""
    if business_closed:
        return "unqualified", [{"code": "closed", "text": "Listed as permanently closed"}]
    for r in regulatory:
        if r["classification"] == NATIONAL_APPROVAL and r["status"] == "verified_by_human":
            return "eligible_verified", [{"code": "verified", "text": f"Confirmed by a person on the aviation authority's site "
                                          f"{r.get('approval_ref') or ''}".strip(), "source_url": r.get("source_url")}]
    signals = (profile or {}).get("signals") or {}
    if signals.get("helicopter") and not signals.get("aeroplane") and not signals.get("has_cpl"):
        return "unqualified", [{"code": "helicopter_only", "text": "Website mentions helicopters only (no aeroplane training)"}]
    if signals.get("drone") and not signals.get("aeroplane") and not signals.get("has_cpl"):
        return "unqualified", [{"code": "drone_only", "text": "Website is about drones, not pilot training"}]
    if pages_read > 0:
        return "eligible_claimed", [{"code": "no_rules", "text": f"No approval register for {country} set up yet: check the "
                                     f"school's approval on the national aviation authority's site"}]
    if (ctx or {}).get("site_exhausted"):
        return "eligible_claimed", [{"code": "no_website", "text": "No website found (address guess, Tavily and Google search all "
                                     "tried): check the school by hand"}]
    return "unknown", [{"code": "no_rules", "text": f"No eligibility rules set for {country} yet"}]


LIST_FRESH_DAYS = 180          # an uploaded list older than this no longer proves that a school is absent from it
RULES_VERSION = "2026-11-05.verify"


def lists_loaded(conn) -> dict:
    """Official lists that are complete and current: {list kind: 'YYYY-MM-DD' of the latest import}."""
    out = {k: d.isoformat() for k, d in conn.execute(
        """SELECT kind, max(processed_at)::date FROM flightbridge.register_upload
            WHERE status='imported' AND coalesce((result->>'rows')::int, 0) > 0 AND processed_at > now() - %s * interval '1 day'
            GROUP BY kind""", (LIST_FRESH_DAYS,)).fetchall()}
    row = conn.execute("""SELECT updated_at::date FROM flightbridge.system_setting WHERE key='cricos_sync'
                          AND updated_at > now() - %s * interval '1 day'""", (LIST_FRESH_DAYS,)).fetchone()
    if row:
        out["cricos"] = row[0].isoformat()
    return out


def search_methods(database_url: str | None = None) -> set[str]:
    """Website look-up methods this installation can use (each has to be tried before a school counts as 'no website')."""
    from . import secrets
    m = {"website_guess"}
    try:
        if secrets.get("tavily_api_key", database_url):
            m.add("website_tavily")
        if secrets.get("google_places_api_key", database_url):
            m.add("website_search")
    except Exception:
        pass
    return m


def site_exhausted(conn, org_id, methods: set[str]) -> bool:
    if conn.execute("SELECT website_domain FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()[0]:
        return False
    done = {r[0] for r in conn.execute("""SELECT DISTINCT action FROM flightbridge.audit_log WHERE entity='organisation'
                                          AND entity_id=%s AND action = ANY(%s)""", (str(org_id), list(methods))).fetchall()}
    return done == methods


def link_listed(conn, org_id, rule: dict, texts: list[str] = (), legal_names: list[str] = ()) -> dict | None:
    """Look for a listed school that is this same school under another name (research/listmatch.py).
    Strong match: copy the listed credential to this school (recorded with how it was matched) and return None.
    Weak match: return {name, list, how, source_url} for a person to confirm."""
    from .research import listmatch
    lists = rule.get("lists") or {}
    if not lists:
        return None
    o = conn.execute("SELECT name, city, region, country, website_domain FROM flightbridge.organisation WHERE id=%s",
                     (org_id,)).fetchone()
    if not o or not o[2]:
        return None
    cands = [{"id": r[0], "name": r[1], "city": r[2], "ref": r[3], "list": lists.get(r[4]), "source_url": r[5],
              "classification": r[4], "authority": r[6]} for r in conn.execute(
        """SELECT o.id, o.name, o.city, ra.approval_ref, ra.classification, ra.source_url, ra.authority_code
             FROM flightbridge.organisation o JOIN flightbridge.regulatory_approval ra ON ra.org_id = o.id
            WHERE o.country=%s AND lower(o.region)=lower(%s) AND o.id<>%s AND o.deleted_at IS NULL
              AND ra.status='listed_in_register' AND ra.classification = ANY(%s)
              AND coalesce(ra.source_version, '') NOT LIKE 'Same school as%%'""",
        (o[3], o[2], org_id, list(lists))).fetchall()]
    if not cands:
        return None
    hit = listmatch.match({"name": o[0], "city": o[1], "region": o[2], "domain": o[4]}, cands, texts, legal_names)
    if not hit:
        return None
    strength, c, how = hit
    if strength == "weak":
        return {"name": c["name"], "list": c["list"], "how": how, "source_url": c["source_url"]}
    conn.execute("""INSERT INTO flightbridge.regulatory_approval (org_id, authority_code, classification, approval_ref, status,
                       source_url, source_version, checked_at, next_check_due)
                    VALUES (%s,%s,%s,%s,'listed_in_register',%s,%s,now(),now() + interval '30 days')
                    ON CONFLICT (org_id, authority_code, classification) DO NOTHING""",
                 (org_id, c["authority"], c["classification"], c["ref"], c["source_url"],
                  f"Same school as '{c['name']}' on the list ({how})"[:300]))
    db.audit(conn, "listed_match", "organisation", org_id, {"listed_org": str(c["id"]), "listed_name": c["name"], "how": how})
    return None


def evaluate_org(conn, org_id, ctx: dict | None = None, texts: list[str] = ()) -> str:
    row = conn.execute("SELECT country, org_kind FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
    if not row:
        return "unknown"
    country, kind = row
    if kind == "ground_school":         # demand-hub institutes are not flying schools: not assessed
        return "unknown"
    cur = conn.execute("SELECT eligibility, eligibility_reasons FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
    if cur[0] == "unqualified" and any(r.get("code") == "human_unqualified" for r in (cur[1] or [])):
        return "unqualified"            # a person's decision is never overwritten by automation
    ctx = dict(ctx) if ctx else {"lists_loaded": lists_loaded(conn), "methods": {"website_guess", "website_tavily", "website_search"}}
    rules = load_rules()
    rule = rules.get(country)

    def regulatory():
        return [dict(zip(("classification", "status", "authority_code", "approval_ref", "source_url"), r)) for r in conn.execute(
            """SELECT classification, status, authority_code, approval_ref, source_url FROM flightbridge.regulatory_approval
               WHERE org_id=%s""", (org_id,)).fetchall()]
    reg = regulatory()
    prof = conn.execute("SELECT signals, evidence, ai FROM flightbridge.organisation_profile WHERE org_id=%s", (org_id,)).fetchone()
    profile = {"signals": prof[0], "evidence": prof[1], "ai": prof[2]} if prof else None
    if rule and not any(r["classification"] in rule["verified"] and r["status"] in ("listed_in_register", "verified_by_human")
                        for r in reg):
        legal = ((profile or {}).get("ai") or {}).get("legal_names") or []
        ctx["lookalike"] = link_listed(conn, org_id, rule, texts, legal)
        reg = regulatory()
    pages = conn.execute("SELECT count(*) FROM flightbridge.website_page WHERE org_id=%s AND http_status=200", (org_id,)).fetchone()[0]
    closed = conn.execute("""SELECT 1 FROM flightbridge.audit_log WHERE entity='organisation' AND entity_id=%s
                             AND action='place_closed' LIMIT 1""", (str(org_id),)).fetchone() is not None
    ctx["site_exhausted"] = not pages and site_exhausted(conn, org_id, ctx.get("methods") or {"website_guess"})
    decision, reasons = evaluate(country, reg, profile, pages, closed, rules, ctx)
    old = cur[0]
    conn.execute("""UPDATE flightbridge.organisation SET eligibility=%s, eligibility_reasons=%s, eligibility_checked_at=now()
                    WHERE id=%s""", (decision, json.dumps(reasons), org_id))
    if old != decision:
        db.audit(conn, "eligibility_set", "organisation", org_id, {"from": old, "to": decision, "reasons": reasons})
    return decision


def refresh_rules(conn) -> bool:
    """When the rules, this code's rule version or the set of loaded official lists change, every school not decided
    by a person (or already verified) is decided again. Returns True when a re-check was queued."""
    import hashlib
    loaded = lists_loaded(conn)
    digest = hashlib.sha256((RULES_PATH.read_text(encoding="utf-8") + RULES_VERSION + json.dumps(loaded, sort_keys=True))
                            .encode()).hexdigest()
    row = conn.execute("SELECT value->>'digest' FROM flightbridge.system_setting WHERE key='eligibility_rules'").fetchone()
    if row and row[0] == digest:
        return False
    conn.execute("""UPDATE flightbridge.organisation SET eligibility_checked_at = NULL
                     WHERE deleted_at IS NULL AND eligibility <> 'eligible_verified'
                       AND NOT (eligibility_reasons @> '[{"code": "human_unqualified"}]'::jsonb)""")
    conn.execute("""INSERT INTO flightbridge.system_setting (key, value, updated_at) VALUES ('eligibility_rules', %s::jsonb, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                 (json.dumps({"digest": digest, "version": RULES_VERSION, "lists": loaded}),))
    db.audit(conn, "eligibility_rules_changed", "system", "eligibility", {"version": RULES_VERSION, "lists": loaded})
    return True


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
        if not source_url:
            raise ValueError("Paste the aviation authority page you checked")
        rule = {"confirm_as": {"authority": "NATIONAL_CAA", "classification": NATIONAL_APPROVAL}, "lookup": [[None, source_url]]}
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
    """Re-evaluate schools whose evidence changed since the last check (or never checked, or the rules changed)."""
    out = {"checked": 0, "eligible_verified": 0, "eligible_claimed": 0, "unqualified": 0, "unknown": 0}
    methods = search_methods(database_url)
    with db.tx(database_url) as conn:
        if org_id:
            ids = [org_id]
        else:
            out["rules_changed"] = refresh_rules(conn)
            ids = [r[0] for r in conn.execute("""
                SELECT o.id FROM flightbridge.organisation o
                 WHERE o.deleted_at IS NULL AND o.org_kind <> 'ground_school'
                   AND NOT (o.eligibility_reasons @> '[{"code": "human_unqualified"}]'::jsonb)
                   AND (o.verify_claim_until IS NULL OR o.verify_claim_until < now())
                   AND (o.eligibility_checked_at IS NULL
                        OR o.eligibility_checked_at < greatest(
                             (SELECT max(checked_at) FROM flightbridge.regulatory_approval r WHERE r.org_id=o.id),
                             (SELECT updated_at FROM flightbridge.organisation_profile p WHERE p.org_id=o.id)))
                 ORDER BY o.eligibility_checked_at NULLS FIRST LIMIT %s""", (limit,)).fetchall()]
        ctx = {"lists_loaded": lists_loaded(conn), "methods": methods}
    for oid in ids:                     # one short transaction per school: a long batch never holds locks
        with db.tx(database_url) as conn:
            out[evaluate_org(conn, oid, ctx)] += 1
        out["checked"] += 1
    return out
