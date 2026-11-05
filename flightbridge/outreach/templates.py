"""Owner-pasted outreach templates.

The owner pastes subject + body in the CRM. Only whitelisted placeholders are allowed, the text is checked
against the same banned-claims list as the send-time content gate, and the legal footer is NOT part of the
template (the sending service appends it). Rendering fails loudly if a placeholder has no value.
"""
from __future__ import annotations

import json
import re

from .. import db
from ..compliance.content import BANNED_CLAIMS

ALLOWED_VARS = {"school_name", "city", "state", "country", "credential"}
VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*\}\}")
FOOTER_MARKERS = re.compile(r"(unsubscribe|^\s*--\s*$|\bRef:\s*FB-)", re.I | re.M)
COUNTRY_NAMES = {"US": "the United States", "GB": "the United Kingdom", "CA": "Canada", "AU": "Australia",
                 "IN": "India", "NZ": "New Zealand", "ZA": "South Africa", "IE": "Ireland"}


class TemplateError(ValueError):
    pass


def validate(subject: str, body: str) -> dict:
    errors, warnings = [], []
    subject, body = (subject or "").strip(), (body or "").strip()
    if not subject or len(subject) > 150:
        errors.append("subject must be 1-150 characters")
    if len(body) < 20:
        errors.append("body is too short")
    if re.match(r"^\s*(re|fw|fwd)\s*:", subject, re.I):
        errors.append("subject must not start with Re:/Fwd: (it would look like a reply)")
    for name in sorted({m.group(1) for m in VAR_RE.finditer(subject + body)} - ALLOWED_VARS):
        errors.append(f"unknown placeholder {{{{{name}}}}} (allowed: {', '.join('{{%s}}' % v for v in sorted(ALLOWED_VARS))})")
    stripped = VAR_RE.sub("X", subject + "\n" + body)
    if re.search(r"\{\{|\}\}|\[\[|\]\]|PLACEHOLDER|\bTODO\b|lorem ipsum|<insert", stripped, re.I):
        errors.append("contains an unresolved placeholder or TODO")
    hits = sorted({tag for pat, tag in BANNED_CLAIMS if re.search(pat, stripped, re.I)})
    if hits:
        errors.append("contains claims that are not approved: " + ", ".join(hits))
    if FOOTER_MARKERS.search(body):
        warnings.append("do not add an unsubscribe line, signature divider or Ref: — the system appends the legal footer")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "placeholders": sorted({m.group(1) for m in VAR_RE.finditer(subject + body)} & ALLOWED_VARS)}


def render(subject: str, body: str, values: dict) -> tuple[str, str]:
    def sub(text):
        def rep(m):
            v = values.get(m.group(1))
            if v is None or not str(v).strip():
                raise TemplateError(f"no value for {{{{{m.group(1)}}}}}")
            return str(v).strip()
        return VAR_RE.sub(rep, text)
    return sub(subject.strip()), sub(body.strip())


# {{credential}}: the school's own approval, as an official list or a person confirmed it (never a website claim)
CREDENTIAL_TEXT = {"PART141_FAA": "FAA Part 141 certification", "SEVP_M1": "SEVP certification for international students",
                   "FTU_TC": "Transport Canada Flight Training Unit approval", "DLI_CA": "Designated Learning Institution status",
                   "CRICOS_PROVIDER": "CRICOS registration", "PART141_CASA": "CASA Part 141 approval",
                   "PART142_CASA": "CASA Part 142 approval", "ATO_UK": "UK CAA Approved Training Organisation status",
                   "PART141_NZ": "CAA NZ Part 141 certification", "NZQA_PROVIDER": "NZQA registration",
                   "FTO_DGCA": "DGCA Flying Training Organisation approval",
                   "NATIONAL_APPROVAL": "approval from the national aviation authority"}
CREDENTIAL_ORDER = list(CREDENTIAL_TEXT)
CREDENTIAL_FALLBACK = "pilot training programme"


def org_values(name: str, city: str | None, country: str, region: str | None = None,
               classifications: list[str] | None = None) -> dict:
    cred = next((CREDENTIAL_TEXT[c] for c in CREDENTIAL_ORDER if c in (classifications or [])), CREDENTIAL_FALLBACK)
    country_name = COUNTRY_NAMES.get(country, country)
    return {"school_name": name, "city": city, "state": region or city or country_name, "country": country_name,
            "credential": cred}


def school_values(conn, org_id) -> dict:
    """Placeholder values for one school, read when the draft is made (just before a person approves it)."""
    name, city, country, region = conn.execute(
        "SELECT name, city, country, region FROM flightbridge.organisation WHERE id=%s", (org_id,)).fetchone()
    cls = [r[0] for r in conn.execute("""SELECT classification FROM flightbridge.regulatory_approval WHERE org_id=%s
                                         AND status IN ('listed_in_register','verified_by_human')""", (org_id,)).fetchall()]
    return org_values(name, city, country, region, cls)


def save_template(database_url: str, kind: str, subject: str, body: str, created_by: str | None) -> dict:
    v = validate(subject, body)
    with db.tx(database_url) as conn:
        ver = conn.execute("SELECT coalesce(max(version),0)+1 FROM flightbridge.outreach_template WHERE kind=%s",
                           (kind,)).fetchone()[0]
        tid = conn.execute(
            """INSERT INTO flightbridge.outreach_template (kind, version, subject, body, validation, created_by)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (kind, ver, subject.strip(), body.strip(), json.dumps(v), created_by)).fetchone()[0]
        db.audit(conn, "template_saved", "outreach_template", tid, {"kind": kind, "version": ver, "ok": v["ok"]},
                 actor_type="employee" if created_by else "system", actor_id=created_by)
    return {"id": str(tid), "version": ver, "validation": v}


def approve_template(database_url: str, template_id: str, approved_by: str) -> None:
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT kind, subject, body FROM flightbridge.outreach_template WHERE id=%s FOR UPDATE",
                           (template_id,)).fetchone()
        if not row:
            raise TemplateError("template not found")
        v = validate(row[1], row[2])
        if not v["ok"]:
            raise TemplateError("; ".join(v["errors"]))
        conn.execute("UPDATE flightbridge.outreach_template SET status='archived' WHERE kind=%s AND status='approved'",
                     (row[0],))
        conn.execute("""UPDATE flightbridge.outreach_template SET status='approved', approved_by=%s, approved_at=now(),
                        validation=%s WHERE id=%s""", (approved_by, json.dumps(v), template_id))
        db.audit(conn, "template_approved", "outreach_template", template_id, {"kind": row[0]},
                 actor_type="employee", actor_id=approved_by)


def approved_template(conn, kind: str):
    return conn.execute("SELECT id, subject, body FROM flightbridge.outreach_template WHERE kind=%s AND status='approved'",
                        (kind,)).fetchone()
