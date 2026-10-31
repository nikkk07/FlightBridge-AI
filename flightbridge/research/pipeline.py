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


def import_rows(database_url: str, rows: list[RegisterRow]) -> dict:
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
            if org_id is None:
                org_id = conn.execute(
                    """INSERT INTO flightbridge.organisation (name, country, city, website_domain, status)
                       VALUES (%s,%s,%s,%s,'research_pending') RETURNING id""",
                    (r.name, r.country, r.city, r.website_domain)).fetchone()[0]
                stats["created"] += 1
                db.audit(conn, "organisation_created", "organisation", org_id, {"source": r.source_url})
            else:
                stats["matched"] += 1
            if r.approval_ref:
                conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                                VALUES (%s,%s,%s,%s) ON CONFLICT (scheme, value) DO NOTHING""",
                             (org_id, scheme, r.approval_ref, r.source_url))
            if r.website_domain:
                conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                                VALUES (%s,'domain',%s,%s) ON CONFLICT (scheme, value) DO NOTHING""",
                             (org_id, r.website_domain, r.source_url))
            now = now_utc()
            conn.execute(
                """INSERT INTO flightbridge.regulatory_approval
                     (org_id, authority_code, classification, approval_ref, status, source_url, source_version,
                      checked_at, checked_by, next_check_due)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (org_id, authority_code, classification) DO UPDATE
                     SET approval_ref=EXCLUDED.approval_ref, status=EXCLUDED.status, source_url=EXCLUDED.source_url,
                         source_version=EXCLUDED.source_version, checked_at=EXCLUDED.checked_at,
                         checked_by=EXCLUDED.checked_by, next_check_due=EXCLUDED.next_check_due""",
                (org_id, r.authority, r.classification, r.approval_ref, r.approval_status, r.source_url,
                 r.source_version, now, r.checked_by, now + timedelta(days=REVALIDATE_DAYS[r.approval_status])))
    return stats


def store_crawl(database_url: str, org_id: str, result: CrawlResult) -> dict:
    stats = {"pages": 0, "contacts": 0, "evidence": 0}
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
            if not f.on_site_domain:
                continue   # only addresses on the school's own domain are attributed to the school
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
        conn.execute("UPDATE flightbridge.organisation SET status='research_completed', updated_at=now() "
                     "WHERE id=%s AND status IN ('discovered','research_pending')", (org_id,))
        db.audit(conn, "research_stored", "organisation", org_id, {**stats, "errors": result.errors[:20]})
    return stats


def research_org(database_url: str, org_id: str, fetcher: SafeFetcher, max_pages: int = 12) -> dict:
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT website_domain FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
    if not row or not row[0]:
        return {"error": "no_website_domain"}
    return store_crawl(database_url, org_id, crawl(f"https://{row[0]}/", fetcher, max_pages=max_pages))


def qualify_org(database_url: str, org_id: str, extra_signals: dict | None = None) -> dict:
    with db.tx(database_url) as conn:
        reg = conn.execute("""SELECT status, classification FROM flightbridge.regulatory_approval
                              WHERE org_id=%s ORDER BY checked_at DESC LIMIT 1""", (org_id,)).fetchone()
        contacts = conn.execute("SELECT is_role_address FROM flightbridge.contact WHERE org_id=%s", (org_id,)).fetchall()
        pages = conn.execute("SELECT count(*) FROM flightbridge.website_page WHERE org_id=%s AND http_status=200",
                             (org_id,)).fetchone()[0]
        signals = {"regulatory_status": reg[0] if reg else None, "classification": reg[1] if reg else None,
                   "role_contact": any(c[0] for c in contacts), "any_contact": bool(contacts),
                   "site_active": pages > 0, **(extra_signals or {})}
        q = score(signals)
        conn.execute(
            """INSERT INTO flightbridge.qualification_result
                 (org_id, score, weights_version, breakdown, missing, risks, recommended_action, confidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (org_id, q["score"], q["weights_version"], json.dumps(q["breakdown"]), json.dumps(q["missing"]),
             json.dumps(q["risks"]), q["recommended_action"], q["confidence"]))
        if q["recommended_action"].startswith("reject"):
            conn.execute("UPDATE flightbridge.organisation SET status='rejected', rejection_reason=%s WHERE id=%s",
                         (q["recommended_action"], org_id))
        elif q["recommended_action"] == "draft_outreach_for_approval":
            conn.execute("UPDATE flightbridge.organisation SET status='qualified' WHERE id=%s", (org_id,))
        else:
            conn.execute("UPDATE flightbridge.organisation SET status='needs_human_review' WHERE id=%s", (org_id,))
        db.audit(conn, "qualified", "organisation", org_id, q)
    return q


INITIAL_TEMPLATE_ID = "initial_intro_v1"


def render_initial(org_name: str) -> tuple[str, str]:
    """Uses ONLY the approved-for-outreach fact set (docs/COMPANY_FACTS_RECONCILIATION.md)."""
    subject = f"Student referral partnership enquiry for {org_name}"
    body = (
        f"Dear {org_name} team,\n\n"
        "We One Aviation Academy is based in Dwarka, New Delhi, India. We teach DGCA ground subjects "
        "(theory) in classroom and online batches and provide pilot-career counselling to Indian students.\n\n"
        "We are looking for flying-school partners to which we can refer students for practical flight "
        "training, and we would like to understand whether a referral arrangement with your school is possible.\n\n"
        "Could you let us know who handles international admissions or partnerships?\n\n"
        "Kind regards,\nWe One Aviation Academy"
    )
    return subject, body


def draft_initial(database_url: str, org_id: str) -> str | None:
    """Create one initial-outreach draft + AMBER approval task for the best role contact. Idempotent per contact."""
    with db.tx(database_url) as conn:
        org = conn.execute("SELECT name, status FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
        if not org or org[1] != "qualified":
            return None
        c = conn.execute("""SELECT id FROM flightbridge.contact WHERE org_id=%s
                            ORDER BY is_role_address DESC, discovered_at LIMIT 1""", (org_id,)).fetchone()
        if not c:
            return None
        if conn.execute("SELECT 1 FROM flightbridge.ai_draft WHERE contact_id=%s AND kind='initial'", (c[0],)).fetchone():
            return None
        subject, body = render_initial(org[0])
        d = conn.execute(
            """INSERT INTO flightbridge.ai_draft (org_id, contact_id, kind, risk, intent, summary, subject, body_text,
                                                  evidence, generator)
               VALUES (%s,%s,'initial','AMBER','initial_outreach',%s,%s,%s,%s,%s) RETURNING id""",
            (org_id, c[0], "Initial partnership enquiry (template; approved facts only)", subject, body,
             json.dumps(["docs/COMPANY_FACTS_RECONCILIATION.md#approved-for-outreach-set-pending-sign-off"]),
             INITIAL_TEMPLATE_ID)).fetchone()[0]
        t = conn.execute(
            """INSERT INTO flightbridge.approval_task (draft_id, risk, escalation_reason)
               VALUES (%s,'AMBER','All first-contact emails require human approval in this release') RETURNING id""",
            (d,)).fetchone()[0]
        db.audit(conn, "draft_created", "ai_draft", d, {"approval_task_id": str(t)})
        return str(t)
