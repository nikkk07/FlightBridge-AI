"""Follow-up engine.

Scheduled only after an initial email is actually SENT (never on dry-run). Steps use system_setting
'followup_schedule_days' (default [4, 9, 16]) and land inside local business hours of the recipient's country
on a weekday. A due step creates a draft from the owner's approved 'followup_N' template plus an approval task;
it never sends by itself. Skipped when: thread suppressed, any inbound reply exists, an approval is pending,
contact/org suppressed, or no approved template for that step.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .compliance.suppression import email_domain, email_hash
from .outreach.templates import TemplateError, approved_template, render, school_values

COUNTRY_TZ = {"US": "America/Chicago", "CA": "America/Toronto", "GB": "Europe/London", "IE": "Europe/Dublin",
              "AU": "Australia/Sydney", "NZ": "Pacific/Auckland", "ZA": "Africa/Johannesburg", "IN": "Asia/Kolkata",
              "DE": "Europe/Berlin", "FR": "Europe/Paris", "ES": "Europe/Madrid", "IT": "Europe/Rome",
              "NL": "Europe/Amsterdam", "PL": "Europe/Warsaw", "PT": "Europe/Lisbon", "SE": "Europe/Stockholm"}


def business_time(base: datetime, days: int, country: str, start_h: int = 9, end_h: int = 17) -> datetime:
    tz = ZoneInfo(COUNTRY_TZ.get(country, "UTC"))
    local = (base + timedelta(days=days)).astimezone(tz)
    if local.hour < start_h:
        local = local.replace(hour=start_h, minute=0, second=0, microsecond=0)
    elif local.hour >= end_h:
        local = (local + timedelta(days=1)).replace(hour=start_h, minute=0, second=0, microsecond=0)
    while local.weekday() >= 5:            # Sat/Sun -> Monday
        local = (local + timedelta(days=1)).replace(hour=start_h, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def _setting(conn, key, default):
    row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key=%s", (key,)).fetchone()
    return row[0] if row else default


def schedule_for_sent(conn, outbound_id: str, thread_id, country: str, sent_at: datetime) -> int:
    days = _setting(conn, "followup_schedule_days", [4, 9, 16])
    hours = _setting(conn, "followup_business_hours", {"start": 9, "end": 17})
    n = 0
    for step, d in enumerate(days[:3], start=1):
        due = business_time(sent_at, int(d), country, int(hours["start"]), int(hours["end"]))
        n += conn.execute("""INSERT INTO flightbridge.follow_up_task (thread_id, step, due_at, outbound_id)
                             VALUES (%s,%s,%s,%s) ON CONFLICT (thread_id, step) DO NOTHING""",
                          (thread_id, step, due, outbound_id)).rowcount
    return n


def process_due(database_url: str, now: datetime | None = None, limit: int = 50) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    results = []
    with db.tx(database_url) as conn:
        due = conn.execute("""SELECT id FROM flightbridge.follow_up_task WHERE status='scheduled' AND due_at <= %s
                              ORDER BY due_at LIMIT %s""", (now, limit)).fetchall()
    for (fid,) in due:
        with db.tx(database_url) as conn:
            row = conn.execute(
                """SELECT f.step, f.thread_id, t.status, t.contact_id, t.org_id, c.email, g.name, g.city, g.country, f.status
                     FROM flightbridge.follow_up_task f JOIN flightbridge.email_thread t ON t.id=f.thread_id
                     JOIN flightbridge.contact c ON c.id=t.contact_id JOIN flightbridge.organisation g ON g.id=t.org_id
                    WHERE f.id=%s FOR UPDATE OF f""", (fid,)).fetchone()
            if not row or row[9] != "scheduled":
                continue
            step, thread_id, tstatus, contact_id, org_id, email, name, city, country, _ = row

            def cancel(reason):
                conn.execute("UPDATE flightbridge.follow_up_task SET status='cancelled', cancelled_reason=%s WHERE id=%s",
                             (reason, fid))
                results.append({"id": str(fid), "result": f"cancelled:{reason}"})

            if tstatus == "suppressed":
                cancel("thread_suppressed"); continue
            if conn.execute("SELECT 1 FROM flightbridge.email_message WHERE thread_id=%s AND direction='inbound' AND NOT is_auto_reply",
                            (thread_id,)).fetchone():
                cancel("reply_received"); continue
            if conn.execute("""SELECT 1 FROM flightbridge.suppression WHERE (scope='contact' AND email_hash=%s)
                               OR (scope='organisation' AND org_id=%s) OR (scope='domain' AND domain=%s)""",
                            (email_hash(email), org_id, email_domain(email))).fetchone():
                cancel("suppressed"); continue
            if conn.execute("""SELECT 1 FROM flightbridge.approval_task a JOIN flightbridge.ai_draft d ON d.id=a.draft_id
                               WHERE d.thread_id=%s AND a.status='open'""", (thread_id,)).fetchone():
                results.append({"id": str(fid), "result": "waiting:approval_pending"}); continue
            tpl = approved_template(conn, f"followup_{step}")
            if tpl is None:
                results.append({"id": str(fid), "result": f"waiting:no_approved_followup_{step}_template"}); continue
            try:
                subject, body = render(tpl[1], tpl[2], school_values(conn, org_id))
            except TemplateError as exc:
                results.append({"id": str(fid), "result": f"waiting:{exc}"}); continue
            d = conn.execute(
                """INSERT INTO flightbridge.ai_draft (thread_id, org_id, contact_id, kind, risk, intent, summary, subject, body_text,
                                                      evidence, generator, template_id)
                   VALUES (%s,%s,%s,'followup','AMBER','followup',%s,%s,%s,%s,%s,%s) RETURNING id""",
                (thread_id, org_id, contact_id, f"Follow-up {step} (no reply yet)", subject, body,
                 json.dumps([f"outreach_template:{tpl[0]}"]), f"template:{tpl[0]}", tpl[0])).fetchone()[0]
            conn.execute("INSERT INTO flightbridge.approval_task (draft_id, risk, escalation_reason) VALUES (%s,'AMBER',%s)",
                         (d, f"Follow-up {step}: no reply since the first email"))
            conn.execute("UPDATE flightbridge.follow_up_task SET status='enqueued', draft_id=%s WHERE id=%s", (d, fid))
            db.audit(conn, "followup_drafted", "follow_up_task", fid, {"step": step, "draft_id": str(d)})
            results.append({"id": str(fid), "result": "drafted"})
    return results
