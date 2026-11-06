"""Email Auto-Pilot: sends the owner's approved emails to Eligible schools by itself - slowly, at the right local time,
and stopping on its own at the first sign of trouble.

The owner switches it ON in the CRM (system_setting 'send_autopilot', written by the CRM; the engine only reads it).
Each step (about every 30 seconds on the Mac, between research cycles) does at most ONE email:

  1. off / sending switched off on this engine / kill switch engaged / pre-flight failing / paused -> nothing
  2. today's number reached (warm-up: 10 a day in week 1, 20 in week 2 ... up to the owner's daily number; or the
     owner's fixed number) -> nothing until tomorrow (India time)
  3. the random gap since the last email hasn't passed -> nothing
  4. pick the next email waiting for approval - follow-ups first, then first emails - whose school is inside its own
     weekday business hours; approve it on the owner's behalf with the CURRENT approved wording (decided_via
     'autopilot', decided_by = the person who switched Auto-Pilot on) and hand it to the one sending path, which runs
     every compliance gate, the suppression list and the kill switch exactly as for a person's approval
  5. after the email: a refusal by the mailbox, a bounce rate above 2% (at least 3 bounces in 7 days) or any spam
     complaint pauses Auto-Pilot with the reason; the owner resumes it from the CRM

The engine's own state (pre-flight checklist, pace, pause) is kept in system_setting 'send_autopilot_state'.
Replies to schools are never sent by Auto-Pilot: they always wait for a person.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .localtime import in_business_hours

log = logging.getLogger("flightbridge.autosend")

DEFAULTS = {"enabled": False, "daily_limit": 40, "warmup": True, "gap_min": 4, "gap_max": 11, "hours": [9, 17],
            "by": None, "since": None, "check_requested_at": None, "resume_at": None}
MAX_DAILY = 100
WARMUP_PER_WEEK = 10
HOME_TZ = ZoneInfo("Asia/Kolkata")               # "today" is the owner's day
PREFLIGHT_MAX_AGE = timedelta(hours=12)
BOUNCE_MIN, BOUNCE_RATE = 3, 0.02
DRAFT_EVERY = timedelta(minutes=30)


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def config(conn) -> dict:
    row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='send_autopilot'").fetchone()
    cfg = dict(DEFAULTS)
    if row and isinstance(row[0], dict):
        cfg.update({k: v for k, v in row[0].items() if k in DEFAULTS})
    cfg["daily_limit"] = max(1, min(int(cfg.get("daily_limit") or 40), MAX_DAILY))
    cfg["gap_min"] = max(1, int(cfg.get("gap_min") or 4))
    cfg["gap_max"] = max(cfg["gap_min"], int(cfg.get("gap_max") or 11))
    h = cfg.get("hours") or [9, 17]
    cfg["hours"] = [int(h[0]), int(h[1])] if len(h) == 2 and 0 <= int(h[0]) < int(h[1]) <= 24 else [9, 17]
    return cfg


def state(conn) -> dict:
    row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='send_autopilot_state'").fetchone()
    return dict(row[0]) if row and isinstance(row[0], dict) else {}


def save_state(conn, patch: dict) -> None:
    conn.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('send_autopilot_state', %s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET value = flightbridge.system_setting.value || EXCLUDED.value,
                    updated_at = now()""", (json.dumps(patch, default=str),))


def today_limit(cfg: dict, st: dict, today: date) -> tuple[int, int | None]:
    """(emails allowed today, warm-up week or None)."""
    if not cfg["warmup"]:
        return cfg["daily_limit"], None
    first = st.get("first_sent_on")
    week = 1 if not first else (today - date.fromisoformat(first)).days // 7 + 1
    return min(cfg["daily_limit"], WARMUP_PER_WEEK * week), week


def day_start(now: datetime) -> datetime:
    return now.astimezone(HOME_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def sent_today(conn, now: datetime) -> int:
    return conn.execute("""SELECT count(*) FROM flightbridge.outbound_message WHERE kind IN ('initial','followup')
                           AND status IN ('sending','sent','send_unknown') AND coalesce(sent_at, authorized_at) >= %s""",
                        (day_start(now),)).fetchone()[0]


def health(conn, since: datetime) -> dict:
    """Sending health since `since` (at most 7 days back): sent, bounces, complaints, mailbox refusals."""
    since = max(since, datetime.now(timezone.utc) - timedelta(days=7))
    sent = conn.execute("""SELECT count(*) FROM flightbridge.outbound_message WHERE status IN ('sent','send_unknown')
                           AND sent_at >= %s""", (since,)).fetchone()[0]
    bounces = conn.execute("""SELECT count(*) FROM flightbridge.email_message WHERE direction='inbound' AND is_bounce
                              AND occurred_at >= %s""", (since,)).fetchone()[0]
    complaints = conn.execute("""SELECT count(*) FROM flightbridge.ai_classification c JOIN flightbridge.email_message m
                                 ON m.id=c.message_id WHERE c.intent='complaint' AND m.occurred_at >= %s""", (since,)).fetchone()[0]
    refused = conn.execute("""SELECT o.last_error FROM flightbridge.outbound_message o
                              JOIN flightbridge.approval_task a ON a.id=o.approval_task_id
                              WHERE a.decided_via='autopilot' AND o.status IN ('failed','send_unknown')
                                AND a.decided_at >= %s ORDER BY a.decided_at DESC LIMIT 1""", (since,)).fetchone()
    return {"sent": sent, "bounces": bounces, "complaints": complaints, "refused": refused[0] if refused else None}


def pause_reason(h: dict) -> str | None:
    if h["complaints"]:
        return "A school called the email spam or complained. Read it in the Inbox, then resume."
    if h["refused"]:
        return f"The mailbox refused or couldn't confirm an email ({h['refused']}). Check the mailbox, then resume."
    if h["bounces"] >= BOUNCE_MIN and h["bounces"] > BOUNCE_RATE * max(h["sent"], 1):
        return (f"{h['bounces']} of {h['sent']} emails bounced in the last 7 days (over 2%). "
                "Bad addresses hurt your domain; check them, then resume.")
    return None


def _candidates(conn, limit: int = 300) -> list[tuple]:
    """Emails waiting for approval that Auto-Pilot may approve: first emails (Eligible schools) and follow-ups,
    made from an owner's template, to a contact that hasn't bounced. Follow-ups first (they are time-bound)."""
    return conn.execute(
        """SELECT a.id, d.kind, g.country, g.region, g.id, f.step
             FROM flightbridge.approval_task a
             JOIN flightbridge.ai_draft d ON d.id = a.draft_id
             JOIN flightbridge.organisation g ON g.id = d.org_id
             JOIN flightbridge.contact c ON c.id = d.contact_id
        LEFT JOIN flightbridge.follow_up_task f ON f.draft_id = d.id
            WHERE a.status = 'open' AND d.kind IN ('initial','followup') AND d.generator LIKE 'template:%%'
              AND g.deleted_at IS NULL AND g.status <> 'rejected'
              AND (d.kind = 'followup' OR g.eligibility = 'eligible_verified')
              AND c.verification_level IS DISTINCT FROM 'bounced' AND (d.kind = 'initial' OR f.step IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM flightbridge.email_thread t WHERE t.id = d.thread_id AND t.status = 'suppressed')
            ORDER BY (d.kind = 'followup') DESC, a.created_at LIMIT %s""", (limit,)).fetchall()


def _claim(conn, now: datetime, until: datetime) -> bool:
    """Atomic: only one runner may send at a time, and only once the gap has passed."""
    return conn.execute(
        """UPDATE flightbridge.system_setting SET value = value || jsonb_build_object('next_send_at', %s::text), updated_at = now()
            WHERE key = 'send_autopilot_state'
              AND coalesce((value->>'next_send_at')::timestamptz, '-infinity'::timestamptz) <= %s RETURNING 1""",
        (until.isoformat(), now)).fetchone() is not None


def _approve(conn, task_id, kind: str, step, org_id, by) -> bool:
    """Approve on the owner's behalf with the wording approved NOW (a newer template version wins)."""
    from .outreach.templates import TemplateError, approved_template, render, school_values
    tpl = approved_template(conn, "initial" if kind == "initial" else f"followup_{step}")
    if tpl is None:
        return False
    try:
        subject, body = render(tpl[1], tpl[2], school_values(conn, org_id))
    except TemplateError:
        return False
    n = conn.execute("""UPDATE flightbridge.approval_task SET status='approved', decided_via='autopilot', decided_by=%s,
                               decided_at=now(), final_subject=%s, final_body=%s,
                               decision_note='Approved by Email Auto-Pilot (owner''s approved template)'
                         WHERE id=%s AND status='open'""", (by, subject, body, task_id)).rowcount
    if n:
        db.audit(conn, "autopilot_approved", "approval_task", task_id, {"template_id": str(tpl[0]), "kind": kind})
    return bool(n)


def _kill_engaged(conn) -> bool:
    row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='outbound_kill_switch'").fetchone()
    return row is None or row[0] is not False


def step(settings, sender=None, dns=None, now: datetime | None = None, rng: random.Random | None = None) -> dict:
    """One Auto-Pilot step. Returns {'state': ...} describing what it did or why it waited."""
    from . import deliverability
    now = now or datetime.now(timezone.utc)
    rng = rng or random.Random()
    url = settings.database_url
    with db.tx(url) as conn:
        cfg, st = config(conn), state(conn)

    # pre-flight: on request (the CRM's "Check now" / switching ON), and every 12 hours while ON
    pre = st.get("preflight") or {}
    asked = _ts(cfg.get("check_requested_at"))
    stale = not pre or (_ts(pre.get("at")) or now) < now - PREFLIGHT_MAX_AGE
    if (asked and asked > (_ts(pre.get("at")) or datetime.min.replace(tzinfo=timezone.utc))) or (cfg["enabled"] and stale):
        pre = deliverability.run(url, settings.may_transmit, dns)

    def report(state_name: str, **kw) -> dict:
        out = {"state": state_name, **kw}
        last_at = _ts(st.get("status_at"))
        if (st.get("status"), st.get("status_detail")) != (state_name, kw.get("detail")) or not last_at \
                or abs((now - last_at).total_seconds()) >= 60:            # unchanged state: write at most once a minute
            with db.tx(url) as conn:
                save_state(conn, {"status": state_name, "status_detail": kw.get("detail"), "status_at": now.isoformat()})
        return out

    if not cfg["enabled"]:
        return report("off")
    if not cfg.get("by"):
        return report("blocked", detail="Switch Auto-Pilot off and on again in the CRM (no owner recorded)")
    if not settings.may_transmit:
        return report("waiting", detail="Sending is switched off on this engine (DRY_RUN / OUTBOUND_ENABLED)")
    with db.tx(url) as conn:
        if _kill_engaged(conn):
            return report("waiting", detail="The kill switch is on")
    if not pre.get("ok"):
        failing = [c["label"] for c in pre.get("checks") or [] if c["status"] == "fail"]
        return report("blocked", detail="Fix first: " + "; ".join(failing) if failing else "Pre-flight not run yet")

    # pause on trouble (only events after the last resume count)
    resume = _ts(cfg.get("resume_at"))
    with db.tx(url) as conn:
        st = state(conn)
        paused_at = _ts(st.get("paused_at"))
        if paused_at and not (resume and resume > paused_at):
            return {"state": "paused", "detail": st.get("pause_reason")}
        if paused_at:
            save_state(conn, {"paused_at": None, "pause_reason": None})
        since = max(filter(None, [_ts(cfg.get("since")), resume, now - timedelta(days=7)]))
        why = pause_reason(health(conn, since))
        if why:
            save_state(conn, {"paused_at": now.isoformat(), "pause_reason": why, "status": "paused", "status_detail": why})
            db.audit(conn, "autopilot_paused", "system_setting", "send_autopilot", {"reason": why})
            return {"state": "paused", "detail": why}
        today = now.astimezone(HOME_TZ).date()
        limit, week = today_limit(cfg, st, today)
        done = sent_today(conn, now)
        save_state(conn, {"today": {"date": today.isoformat(), "sent": done, "limit": limit, "warmup_week": week}})
    if done >= limit:
        return report("done_today", detail=f"{done} of {limit} sent today", sent=done, limit=limit)
    nxt = _ts(st.get("next_send_at"))
    if nxt and nxt > now:
        return report("gap", detail=f"next email after {nxt.isoformat(timespec='minutes')}", next_send_at=nxt.isoformat())

    with db.tx(url) as conn:
        cands = _candidates(conn)
    if not cands:
        last = _ts(st.get("last_draft_at"))
        if not last or last < now - DRAFT_EVERY:            # make first emails for newly eligible schools
            from .automation import draft, qualify
            try:
                qualify(url, 20)
                made = draft(url, 20).get("drafts_created", 0)
            except Exception as exc:                        # drafting trouble must not stop the step
                log.warning("autopilot drafting failed: %s", exc)
                made = 0
            with db.tx(url) as conn:
                save_state(conn, {"last_draft_at": now.isoformat()})
            if made:
                return report("drafted", detail=f"{made} new emails ready")
        return report("idle", detail="No approved school is waiting for an email")
    with db.tx(url) as conn:
        kinds = {r[0] for r in conn.execute("SELECT kind FROM flightbridge.outreach_template WHERE status='approved'")}
    cands = [c for c in cands if ("initial" if c[1] == "initial" else f"followup_{c[5]}") in kinds]
    if not cands:
        return report("idle", detail="Emails are waiting, but their template isn't approved")
    lo, hi = cfg["hours"]
    pick = next((c for c in cands if in_business_hours(now, c[2], c[3], lo, hi)), None)
    if pick is None:
        return report("waiting_hours", detail=f"{len(cands)} emails ready; waiting for business hours at the schools")

    gap = timedelta(minutes=rng.uniform(cfg["gap_min"], cfg["gap_max"]))
    with db.tx(url) as conn:
        if not _claim(conn, now, now + gap):
            return {"state": "busy", "detail": "another runner is sending"}
        if _kill_engaged(conn):
            save_state(conn, {"next_send_at": now.isoformat()})
            return report("waiting", detail="The kill switch is on")
        ok = _approve(conn, pick[0], pick[1], pick[5], pick[4], cfg["by"])
    if not ok:
        with db.tx(url) as conn:
            save_state(conn, {"next_send_at": now.isoformat()})
        return report("skipped", detail="That email's template is no longer approved")

    if sender is None:
        from .compliance.rules import load_rules
        from .sending.service import SendingService
        from .settings import load_sender_profile
        sender = SendingService(settings, load_rules(), load_sender_profile())
    oid = sender.enqueue_from_approval(str(pick[0]))
    res = sender.process(oid) if oid else None
    status = res.status if res else "missing"
    with db.tx(url) as conn:
        patch = {"last": {"at": now.isoformat(), "status": status, "kind": pick[1], "org_id": str(pick[4]),
                          "outbound_id": oid, "reasons": (res.reasons if res else [])[:5]}}
        if status == "sent":
            if not st.get("first_sent_on"):
                patch["first_sent_on"] = now.astimezone(HOME_TZ).date().isoformat()
        elif status == "deferred":                     # this computer can't reach the mail server: try again soon
            patch["next_send_at"] = (now + timedelta(minutes=5)).isoformat()
        elif status in ("failed", "send_unknown"):
            why = f"The mailbox refused or couldn't confirm an email ({'; '.join(res.reasons) if res else status}). Check the mailbox, then resume."
            patch.update({"paused_at": now.isoformat(), "pause_reason": why})
            db.audit(conn, "autopilot_paused", "system_setting", "send_autopilot", {"reason": why})
        else:                                          # blocked / needs review by the gates: nothing went out
            patch["next_send_at"] = now.isoformat()
        save_state(conn, patch)
    return report("sent" if status == "sent" else status, outbound_id=oid, kind=pick[1],
                  detail=None if status == "sent" else ", ".join(res.reasons if res else []))
