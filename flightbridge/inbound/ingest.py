"""Store inbound mail exactly once, attach it to the right thread, and apply deterministic effects.

Thread matching (first hit wins): In-Reply-To/References -> stored Message-ID; our "Ref: FB-..." token in the
body (replies usually quote it); sender address == a known contact; sender's company domain == a contact's domain
(never for shared providers such as gmail.com); else an unmatched thread for a human.
Effects: bounce -> contact marked bounced + contact suppression; unsubscribe -> suppression of the sender (and the
thread's contact) + follow-ups cancelled; rejection -> contact suppression + follow-ups cancelled; any genuine
reply -> scheduled follow-ups cancelled. Then classification + a draft + an approval task (never an automatic send).
"""
from __future__ import annotations

import json
import re

from .. import db
from ..ai.classify import Classification, classify
from ..ai.drafting import build_reply, load_facts, reply_subject
from ..compliance.suppression import email_hash
from .readers import InboundMessage

REF_RE = re.compile(r"\bFB-[0-9A-F]{12}\b")


def _suppress_contact(conn, email: str, reason: str, ref: str) -> None:
    conn.execute("""INSERT INTO flightbridge.suppression (scope, email_hash, reason, source_ref)
                    VALUES ('contact', %s, %s, %s) ON CONFLICT DO NOTHING""", (email_hash(email), reason, ref))


def cancel_followups(conn, thread_id, reason: str) -> int:
    if thread_id is None:
        return 0
    return conn.execute("""UPDATE flightbridge.follow_up_task SET status='cancelled', cancelled_reason=%s
                           WHERE thread_id=%s AND status='scheduled'""", (reason, thread_id)).rowcount


def _match_thread(conn, m: InboundMessage):
    ids = [i for i in ([m.in_reply_to] + list(m.references)) if i]
    if ids:
        row = conn.execute("SELECT thread_id FROM flightbridge.email_message WHERE rfc_message_id = ANY(%s) LIMIT 1",
                           (ids,)).fetchone()
        if row:
            return row[0], "in_reply_to"
    for tok in REF_RE.findall(m.text or ""):
        row = conn.execute("""SELECT o.thread_id FROM flightbridge.outbound_message o
                              WHERE o.ref_token=%s AND o.thread_id IS NOT NULL""", (tok,)).fetchone()
        if row:
            return row[0], "ref_token"
    if m.from_address:
        row = conn.execute("""SELECT t.id FROM flightbridge.email_thread t JOIN flightbridge.contact c ON c.id=t.contact_id
                              WHERE c.email=%s ORDER BY t.updated_at DESC LIMIT 1""", (m.from_address,)).fetchone()
        if row:
            return row[0], "sender_address"
        dom = m.from_address.rsplit("@", 1)[-1]
        if not conn.execute("SELECT 1 FROM flightbridge.public_mail_domain WHERE domain=%s", (dom,)).fetchone():
            row = conn.execute("""SELECT t.id FROM flightbridge.email_thread t JOIN flightbridge.contact c ON c.id=t.contact_id
                                  WHERE c.email_domain=%s ORDER BY t.updated_at DESC LIMIT 1""", (dom,)).fetchone()
            if row:
                return row[0], "sender_domain"
    return None, "unmatched"


def ingest(conn, m: InboundMessage) -> str | None:
    """Insert one message (idempotent). Returns the new email_message id, or None if already stored."""
    if conn.execute("""SELECT 1 FROM flightbridge.email_message WHERE (provider_folder=%s AND provider_uid=%s AND mailbox=%s)
                       OR (rfc_message_id IS NOT NULL AND rfc_message_id=%s)""",
                    (m.folder, m.uid, m.mailbox, m.message_id)).fetchone():
        return None
    thread_id, how = _match_thread(conn, m)
    if thread_id is None:
        thread_id = conn.execute("""INSERT INTO flightbridge.email_thread (subject_normalised, root_message_id, status)
                                    VALUES (%s,%s,'open') RETURNING id""", (m.subject[:300], m.message_id)).fetchone()[0]
    mid = conn.execute(
        """INSERT INTO flightbridge.email_message
             (thread_id, direction, rfc_message_id, in_reply_to, references_ids, provider_uid, provider_folder, mailbox,
              from_address, to_addresses, subject, body_text, occurred_at, match_method)
           VALUES (%s,'inbound',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s, now()),%s)
           ON CONFLICT DO NOTHING RETURNING id""",
        (thread_id, m.message_id, m.in_reply_to, list(m.references), m.uid, m.folder, m.mailbox, m.from_address,
         list(m.to), m.subject[:1000], m.text, m.date, how)).fetchone()
    if mid is None:
        return None
    conn.execute("UPDATE flightbridge.email_thread SET updated_at=now() WHERE id=%s", (thread_id,))
    db.audit(conn, "inbound_stored", "email_message", mid[0], {"match": how})
    return str(mid[0])


def process_message(conn, message_id: str, headers: dict | None = None, llm=None) -> Classification:
    """Classify, apply effects, draft. Runs once per message (guarded by processed_at)."""
    row = conn.execute(
        """SELECT m.thread_id, m.from_address, m.subject, m.body_text, m.processed_at, t.contact_id, t.org_id,
                  c.email, g.name
             FROM flightbridge.email_message m JOIN flightbridge.email_thread t ON t.id = m.thread_id
        LEFT JOIN flightbridge.contact c ON c.id = t.contact_id
        LEFT JOIN flightbridge.organisation g ON g.id = t.org_id
            WHERE m.id=%s FOR UPDATE OF m""", (message_id,)).fetchone()
    if row is None or row[4] is not None:
        return None
    thread_id, sender, subject, body, _, contact_id, org_id, contact_email, org_name = row
    c = classify(subject, body or "", sender, headers)
    if llm is not None and not (c.is_bounce or c.is_auto_reply):
        from ..ai.llm import merge
        c = merge(c, llm(subject, c.new_text))
    ref = f"email_message:{message_id}"

    if c.is_bounce:
        for tok in REF_RE.findall(body or ""):
            hit = conn.execute("""SELECT c.id, c.email, o.thread_id FROM flightbridge.outbound_message o
                                  JOIN flightbridge.contact c ON c.id=o.contact_id WHERE o.ref_token=%s""", (tok,)).fetchone()
            if hit:
                conn.execute("UPDATE flightbridge.contact SET verification_level='bounced' WHERE id=%s", (hit[0],))
                _suppress_contact(conn, hit[1], "hard_bounce", ref)
                cancel_followups(conn, hit[2], "bounce")
                conn.execute("UPDATE flightbridge.email_thread SET status='suppressed' WHERE id=%s", (hit[2],))
    elif c.is_auto_reply:
        pass                                    # out-of-office: keep the schedule, no reply
    else:
        cancel_followups(conn, thread_id, "reply_received")
        if c.is_unsubscribe or c.intent == "partnership_rejection":
            reason = "unsubscribe" if c.is_unsubscribe else "rejection"
            for addr in {a for a in (sender, contact_email) if a}:
                _suppress_contact(conn, addr, reason, ref)
            conn.execute("UPDATE flightbridge.email_thread SET status='suppressed' WHERE id=%s", (thread_id,))
            if c.intent == "partnership_rejection" and org_id:
                conn.execute("""UPDATE flightbridge.organisation SET status='rejected', rejection_reason='declined_by_school'
                                WHERE id=%s AND status NOT IN ('partner','negotiation')""", (org_id,))
        elif org_id and c.intent == "positive_interest":
            conn.execute("UPDATE flightbridge.organisation SET status='interested' WHERE id=%s AND status IN ('contacted','qualified','ready_for_outreach')",
                         (org_id,))

    conn.execute(
        """INSERT INTO flightbridge.ai_classification (message_id, intent, risk, confidence, signals, questions, injection, classifier)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (message_id) DO NOTHING""",
        (message_id, c.intent, c.risk, c.confidence, json.dumps(c.signals), json.dumps(c.questions), c.injection, c.classifier))
    conn.execute("""UPDATE flightbridge.email_message SET is_bounce=%s, is_auto_reply=%s, is_unsubscribe=%s, processed_at=now()
                    WHERE id=%s""", (c.is_bounce, c.is_auto_reply, c.is_unsubscribe, message_id))

    if contact_id and org_id and not (c.is_bounce or c.is_auto_reply):
        plan = build_reply(c.intent, c.risk, c.questions, org_name, load_facts())
        if plan:
            d = conn.execute(
                """INSERT INTO flightbridge.ai_draft (thread_id, org_id, contact_id, inbound_message_id, kind, risk, intent, summary,
                                                      extracted_questions, subject, body_text, evidence, generator)
                   VALUES (%s,%s,%s,%s,'reply',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (thread_id, org_id, contact_id, message_id, c.risk, c.intent,
                 f"{c.intent} ({c.risk}, confidence {c.confidence:.2f}); signals: {', '.join(c.signals) or 'none'}",
                 json.dumps(c.questions), reply_subject(subject), plan["body"], json.dumps(plan["evidence"]),
                 c.classifier)).fetchone()[0]
            conn.execute("INSERT INTO flightbridge.approval_task (draft_id, risk, escalation_reason) VALUES (%s,%s,%s)",
                         (d, c.risk, plan["escalation_reason"]))
            conn.execute("UPDATE flightbridge.email_thread SET status='awaiting_approval' WHERE id=%s", (thread_id,))
    db.audit(conn, "inbound_processed", "email_message", message_id,
             {"intent": c.intent, "risk": c.risk, "signals": c.signals, "injection": c.injection})
    return c


def poll_and_process(database_url: str, reader, folder: str = "INBOX", limit: int = 100, llm=None) -> dict:
    """Read new mail since the cursor, store, process. Cursor advances only after a successful commit."""
    stats = {"fetched": 0, "stored": 0, "processed": 0}
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT last_uid FROM flightbridge.mailbox_cursor WHERE mailbox=%s AND folder=%s",
                           (reader.mailbox, folder)).fetchone()
    last = row[0] if row else 0
    for m in reader.fetch_since(folder, last, limit):
        stats["fetched"] += 1
        with db.tx(database_url) as conn:           # one transaction per message: a crash never loses or doubles mail
            mid = ingest(conn, m)
            if mid:
                stats["stored"] += 1
                if process_message(conn, mid, m.headers, llm):
                    stats["processed"] += 1
            conn.execute("""INSERT INTO flightbridge.mailbox_cursor (mailbox, folder, last_uid) VALUES (%s,%s,%s)
                            ON CONFLICT (mailbox, folder) DO UPDATE SET last_uid=GREATEST(mailbox_cursor.last_uid, EXCLUDED.last_uid),
                            updated_at=now()""", (reader.mailbox, folder, m.uid))
    return stats
