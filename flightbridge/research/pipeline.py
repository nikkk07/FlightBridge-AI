"""DB-backed research pipeline: register import -> crawl -> contacts/evidence -> qualification -> draft + approval task.
Nothing here can send: drafts only become outbound rows after a human approves them in the CRM."""
from __future__ import annotations

import json
import re
from datetime import timedelta

from .. import db
from ..compliance.suppression import email_domain, email_hash
from .crawler import CrawlResult, crawl
from .fetch import SafeFetcher
from .qualification import CPL_RE, PPL_RE, score
from .registers import ID_SCHEME, RegisterRow, now_utc

REVALIDATE_DAYS = {"listed_in_register": 30, "verified_by_human": 90}
AUTO_EVIDENCE_DAYS = 90


def import_rows(database_url: str, rows: list[RegisterRow], match_names: bool = False) -> dict:
    """match_names: also match an existing school by name (same country, and same state when both are known).
    Used for regulator lists that carry no website (e.g. the FAA's), so they don't duplicate schools the world
    search already found."""
    stats = {"created": 0, "matched": 0}
    with db.tx(database_url) as conn:
        for r in rows:
            scheme = ID_SCHEME[r.authority]
            org_id = None
            if r.approval_ref:
                hit = conn.execute("SELECT org_id FROM flightbridge.organisation_identifier WHERE scheme=%s AND value=%s",
                                   (scheme, r.approval_ref)).fetchone()
                org_id = hit[0] if hit else None
            if org_id is None and r.website_domain:
                hit = conn.execute("SELECT id FROM flightbridge.organisation WHERE lower(website_domain)=%s AND deleted_at IS NULL",
                                   (r.website_domain,)).fetchone()
                org_id = hit[0] if hit else None
            if org_id is None and match_names:
                org_id = match_by_name(conn, r.name, r.country, r.region)
            if org_id is None:
                org_id = conn.execute(
                    """INSERT INTO flightbridge.organisation (name, country, region, city, website_domain, status)
                       VALUES (%s,%s,%s,%s,%s,'research_pending') RETURNING id""",
                    (r.name, r.country, r.region, r.city, r.website_domain)).fetchone()[0]
                stats["created"] += 1
                db.audit(conn, "organisation_created", "organisation", org_id, {"source": r.source_url})
            else:
                stats["matched"] += 1
                if r.region:
                    conn.execute("UPDATE flightbridge.organisation SET region=%s WHERE id=%s AND region IS DISTINCT FROM %s",
                                 (r.region, org_id, r.region))
            if r.approval_ref:
                conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                                VALUES (%s,%s,%s,%s) ON CONFLICT (scheme, value) DO NOTHING""",
                             (org_id, scheme, r.approval_ref, r.source_url))
            if r.website_domain:
                conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                                VALUES (%s,'domain',%s,%s) ON CONFLICT (scheme, value) DO NOTHING""",
                             (org_id, r.website_domain, r.source_url))
            if r.phone:
                store_phone(conn, org_id, r.phone, "regulator", r.source_url)
            now = now_utc()
            conn.execute(
                """INSERT INTO flightbridge.regulatory_approval
                     (org_id, authority_code, classification, approval_ref, courses, status, source_url, source_version,
                      checked_at, checked_by, next_check_due)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (org_id, authority_code, classification) DO UPDATE
                     SET approval_ref=EXCLUDED.approval_ref, courses=EXCLUDED.courses, status=EXCLUDED.status, source_url=EXCLUDED.source_url,
                         source_version=EXCLUDED.source_version, checked_at=EXCLUDED.checked_at,
                         checked_by=EXCLUDED.checked_by, next_check_due=EXCLUDED.next_check_due""",
                (org_id, r.authority, r.classification, r.approval_ref, json.dumps(list(r.courses)), r.approval_status, r.source_url,
                 r.source_version, now, r.checked_by, now + timedelta(days=REVALIDATE_DAYS[r.approval_status])))
    return stats


def match_by_name(conn, name: str, country: str, region: str | None):
    """Same school by name: equal normalised names, or one containing the other (8+ letters), same country;
    when both sides know the state/region it must match too."""
    from .discover import norm_name
    n = norm_name(name)
    if len(n) < 6:
        return None
    best = None
    for oid, other, oreg in conn.execute("SELECT id, name, region FROM flightbridge.organisation WHERE country=%s AND deleted_at IS NULL",
                                         (country,)).fetchall():
        if region and oreg and region.strip().lower() != oreg.strip().lower():
            continue
        on = norm_name(other)
        if on == n:
            return oid
        if best is None and len(on) >= 8 and (on in n or n in on):
            best = oid
    return best


def store_phone(conn, org_id, raw: str, source: str, source_url: str | None) -> bool:
    """Save one phone number (digits-only key de-duplicates formats). Returns True if new."""
    from .signals import _digits
    d = _digits(raw or "")
    if not d:
        return False
    row = conn.execute("""INSERT INTO flightbridge.contact_phone (org_id, phone, display, source, source_url)
                          VALUES (%s,%s,%s,%s,%s) ON CONFLICT (org_id, phone) DO NOTHING RETURNING id""",
                       (org_id, d, " ".join(raw.split())[:40], source, source_url)).fetchone()
    return row is not None


def store_channel(conn, org_id, kind: str, url: str, source_url: str | None) -> bool:
    row = conn.execute("""INSERT INTO flightbridge.contact_channel (org_id, kind, url, source_url) VALUES (%s,%s,%s,%s)
                          ON CONFLICT (org_id, kind, url) DO NOTHING RETURNING id""",
                       (org_id, kind, url[:500], source_url)).fetchone()
    return row is not None


def store_crawl(database_url: str, org_id: str, result: CrawlResult, profile: dict | None = None) -> dict:
    stats = {"pages": 0, "contacts": 0, "evidence": 0, "phones": 0}
    text_signals = {"has_cpl": False, "has_ppl": False}
    with db.tx(database_url) as conn:
        for url, status, allowed, sha in result.pages:
            conn.execute(
                """INSERT INTO flightbridge.website_page (org_id, url, http_status, robots_allowed, content_sha256)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (url) DO UPDATE
                   SET http_status=EXCLUDED.http_status, robots_allowed=EXCLUDED.robots_allowed,
                       content_sha256=EXCLUDED.content_sha256, fetched_at=now()""",
                (org_id, url, status, allowed, sha))
            stats["pages"] += 1
        for f in result.findings:
            if not (f.on_site_domain or f.freemail):
                continue   # only addresses on the school's own domain(s), or a free-mail address it publishes itself
            if re.search(CPL_RE, f.context, re.I):
                text_signals["has_cpl"] = True
            cid = conn.execute(
                """INSERT INTO flightbridge.contact (org_id, email, email_hash, email_domain, is_role_address, source_url)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (org_id, email_hash) DO UPDATE SET source_url=EXCLUDED.source_url RETURNING id""",
                (org_id, f.email, email_hash(f.email), email_domain(f.email), f.is_role_address, f.source_url)).fetchone()[0]
            stats["contacts"] += 1
            expires = now_utc() + timedelta(days=AUTO_EVIDENCE_DAYS)
            conn.execute(
                """INSERT INTO flightbridge.contact_fact_evidence (contact_id, fact, source_url, excerpt, method, expires_at)
                   VALUES (%s,'address_conspicuously_published',%s,%s,'automated_extraction',%s)""",
                (cid, f.source_url, f.context[:500], expires))
            stats["evidence"] += 1
            if f.refusal_nearby:
                conn.execute(
                    """INSERT INTO flightbridge.contact_fact_evidence (contact_id, fact, source_url, excerpt, method, expires_at)
                       VALUES (%s,'refusal_statement_present',%s,%s,'automated_extraction',%s)""",
                    (cid, f.source_url, f.context[:500], now_utc() + timedelta(days=3650)))
                stats["evidence"] += 1
        for ph in getattr(result, "phones", []) or []:
            stats["phones"] += store_phone(conn, org_id, ph["display"], "website", ph["source_url"])
        stats["channels"] = 0
        for kind, url, src in getattr(result, "socials", []) or []:
            stats["channels"] += store_channel(conn, org_id, kind, url, src)
        for url in getattr(result, "contact_forms", []) or []:
            stats["channels"] += store_channel(conn, org_id, "contact_form", url, url)
        conn.execute("UPDATE flightbridge.organisation SET contacts_searched_at=now() WHERE id=%s", (org_id,))
        if profile is not None:
            conn.execute(
                """INSERT INTO flightbridge.organisation_profile (org_id, summary, signals, evidence, generator, updated_at)
                   VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (org_id) DO UPDATE
                   SET summary=EXCLUDED.summary, signals=EXCLUDED.signals, evidence=EXCLUDED.evidence,
                       generator=EXCLUDED.generator, updated_at=now(), ai=NULL""",
                (org_id, profile.get("summary"), json.dumps(profile["signals"]), json.dumps(profile["evidence"]),
                 profile.get("generator", "keywords-v1")))
            stats["signals"] = sorted(k for k, v in profile["signals"].items() if v)
        conn.execute("UPDATE flightbridge.organisation SET status='research_completed', updated_at=now() "
                     "WHERE id=%s AND status IN ('discovered','research_pending')", (org_id,))
        db.audit(conn, "research_stored", "organisation", org_id, {**stats, "errors": result.errors[:20]})
    return stats


def research_org(database_url: str, org_id: str, fetcher: SafeFetcher, max_pages: int = 14, deep: bool = False,
                 keep_texts: bool = False) -> dict:
    """Read the school's website. deep=True (contact hunt) tries the usual contact/about/privacy paths first and reads more pages."""
    from .signals import extract, summarise
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT website_domain, name FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
    if not row or not row[0]:
        return {"error": "no_website_domain"}
    result = crawl(f"https://{row[0]}/", fetcher, max_pages=28 if deep else max_pages, seed_contact_paths=True)
    if not result.pages or all(st == 0 for _, st, _, _ in result.pages):
        alt = row[0][4:] if row[0].startswith("www.") else f"www.{row[0]}"     # robots/TLS on the other host name
        retry = crawl(f"https://{alt}/", fetcher, max_pages=28 if deep else max_pages, seed_contact_paths=True)
        if retry.pages and any(st for _, st, _, _ in retry.pages):
            result = retry
    profile = extract(result.texts)
    profile["summary"], profile["generator"] = summarise(row[1], result.texts)
    stats = store_crawl(database_url, org_id, result, profile)
    if keep_texts:           # the Verify worker passes the page text to the AI check and the list matcher
        stats["texts"], stats["evidence_list"] = [t for _, t in result.texts], profile.get("evidence") or []
    return stats


def qualify_org(database_url: str, org_id: str, extra_signals: dict | None = None) -> dict:
    with db.tx(database_url) as conn:
        reg = conn.execute("""SELECT status, classification FROM flightbridge.regulatory_approval
                              WHERE org_id=%s ORDER BY checked_at DESC LIMIT 1""", (org_id,)).fetchone()
        contacts = conn.execute("SELECT is_role_address FROM flightbridge.contact WHERE org_id=%s", (org_id,)).fetchall()
        pages = conn.execute("SELECT count(*) FROM flightbridge.website_page WHERE org_id=%s AND http_status=200",
                             (org_id,)).fetchone()[0]
        courses = conn.execute("SELECT courses FROM flightbridge.regulatory_approval WHERE org_id=%s", (org_id,)).fetchall()
        course_text = " ".join(str(c) for row in courses for c in (row[0] or []))
        prof = conn.execute("SELECT signals FROM flightbridge.organisation_profile WHERE org_id=%s", (org_id,)).fetchone()
        site = (prof[0] if prof else None) or {}
        signals = {"regulatory_status": reg[0] if reg else None, "classification": reg[1] if reg else None,
                   "role_contact": any(c[0] for c in contacts), "any_contact": bool(contacts),
                   "site_active": pages > 0,
                   # CPL/PPL: the regulator's approved-course list is primary evidence; the website is secondary.
                   "has_cpl": bool(re.search(CPL_RE, course_text, re.I)) or bool(site.get("has_cpl")),
                   "has_ppl": bool(re.search(PPL_RE, course_text, re.I)) or bool(site.get("has_ppl"))}
        if prof:   # only claim these as known when the website was actually read
            signals["intl_students"] = bool(site.get("intl_students"))
            signals["fleet_published"] = bool(site.get("fleet_published"))
        signals.update(extra_signals or {})
        q = score(signals)
        conn.execute(
            """INSERT INTO flightbridge.qualification_result
                 (org_id, score, weights_version, breakdown, missing, risks, recommended_action, confidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (org_id, q["score"], q["weights_version"], json.dumps(q["breakdown"]), json.dumps(q["missing"]),
             json.dumps(q["risks"]), q["recommended_action"], q["confidence"]))
        if q["recommended_action"].startswith("reject"):
            # Automation never rejects a regulator-approved school on its own: a person decides (e.g. find a
            # contact by hand). The reason is kept in qualification_result.recommended_action.
            conn.execute("UPDATE flightbridge.organisation SET status='needs_human_review' WHERE id=%s "
                         "AND status NOT IN ('rejected','contacted','interested','negotiation','partner')", (org_id,))
        elif q["recommended_action"] == "draft_outreach_for_approval":
            conn.execute("UPDATE flightbridge.organisation SET status='qualified' WHERE id=%s "
                         "AND status NOT IN ('rejected','contacted','interested','negotiation','partner')", (org_id,))
        else:
            conn.execute("UPDATE flightbridge.organisation SET status='needs_human_review' WHERE id=%s "
                         "AND status NOT IN ('rejected','contacted','interested','negotiation','partner')", (org_id,))
        db.audit(conn, "qualified", "organisation", org_id, q)
    return q


def draft_initial(database_url: str, org_id: str) -> str | None:
    """Create one initial-outreach draft + AMBER approval task from the owner's APPROVED 'initial' template.
    No approved template => no draft (the system never writes its own outreach text). Idempotent per contact."""
    from ..outreach.templates import TemplateError, approved_template, render, school_values
    with db.tx(database_url) as conn:
        org = conn.execute("SELECT name, status, city, country, eligibility FROM flightbridge.organisation WHERE id=%s",
                           (org_id,)).fetchone()
        if not org or org[1] != "qualified":
            return None
        elig = org[4]
        if elig != "eligible_verified":
            from ..eligibility import evaluate_org       # evidence may be newer than the last check
            elig = evaluate_org(conn, org_id)
        if elig != "eligible_verified":
            db.audit(conn, "draft_skipped", "organisation", org_id, {"reason": "eligibility_not_verified", "eligibility": elig})
            return None
        tpl = approved_template(conn, "initial")
        if tpl is None:
            db.audit(conn, "draft_skipped", "organisation", org_id, {"reason": "no_approved_initial_template"})
            return None
        c = conn.execute("""SELECT id FROM flightbridge.contact WHERE org_id=%s
                            ORDER BY is_role_address DESC, discovered_at LIMIT 1""", (org_id,)).fetchone()
        if not c:
            return None
        if conn.execute("SELECT 1 FROM flightbridge.ai_draft WHERE contact_id=%s AND kind='initial'", (c[0],)).fetchone():
            return None
        try:
            subject, body = render(tpl[1], tpl[2], school_values(conn, org_id))
        except TemplateError as exc:
            db.audit(conn, "draft_skipped", "organisation", org_id, {"reason": str(exc)})
            return None
        d = conn.execute(
            """INSERT INTO flightbridge.ai_draft (org_id, contact_id, kind, risk, intent, summary, subject, body_text,
                                                  evidence, generator, template_id)
               VALUES (%s,%s,'initial','AMBER','initial_outreach',%s,%s,%s,%s,%s,%s) RETURNING id""",
            (org_id, c[0], "First contact from the owner's approved template", subject, body,
             json.dumps([f"outreach_template:{tpl[0]}"]), f"template:{tpl[0]}", tpl[0])).fetchone()[0]
        t = conn.execute(
            """INSERT INTO flightbridge.approval_task (draft_id, risk, escalation_reason)
               VALUES (%s,'AMBER','All first-contact emails require human approval in this release') RETURNING id""",
            (d,)).fetchone()[0]
        db.audit(conn, "draft_created", "ai_draft", d, {"approval_task_id": str(t)})
        return str(t)
