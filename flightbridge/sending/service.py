"""The ONE authorised sending path.

Flow per outbound message:
  T1 (row lock): approval check -> suppression -> gate 1 (jurisdiction) -> gate 2 (content)
                 -> persist both checks -> status authorized | blocked | needs_review
                 (DB trigger re-verifies the checks before 'authorized' can be written)
  T2: if DRY_RUN, OUTBOUND_ENABLED=false or DB kill switch -> 'dry_run_rendered' (no transport built)
      else claim 'authorized' -> 'sending' with a conditional UPDATE (exactly-once claim)
  Transport call outside any transaction; result -> sent | failed (definite rejection) | send_unknown.
  send_unknown is never retried automatically; reconcile() checks the Sent folder for the ref token.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Callable

from .. import db
from ..compliance import content as content_gate
from ..compliance import jurisdiction as jurisdiction_gate
from ..compliance.content import OutboundContent, SenderProfile, content_sha256
from ..compliance.jurisdiction import Evidence, RecipientContext
from ..compliance.suppression import SuppressionIndex, email_domain, email_hash
from ..settings import Settings

_SEND_CAPABILITY = object()   # only this module holds it; transports refuse construction without it

APPROVED = ("approved", "edited_approved")


def new_ref_token() -> str:
    return "FB-" + secrets.token_hex(6).upper()


def compose_body(main_text: str, profile: SenderProfile, ref_token: str) -> str:
    """System-owned legal footer appended after the human/AI text. Editors cannot remove it."""
    lines = [main_text.rstrip(), "", "--"]
    for v in (profile.ad_disclosure_text, profile.legal_name, profile.postal_address,
              profile.contact_email, profile.phone, profile.privacy_notice_text, profile.opt_out_text):
        if v:
            lines.append(v)
    lines.append(f"Ref: {ref_token}")
    return "\n".join(lines)


@dataclass
class ProcessOutcome:
    outbound_id: str
    status: str
    reasons: list


class SendingService:
    def __init__(self, settings: Settings, rules: dict, profile: SenderProfile,
                 transport_factory: Callable[..., object] | None = None):
        self.s, self.rules, self.profile = settings, rules, profile
        self._transport_factory = transport_factory
        self._transport = None

    # ------------------------------------------------------------ enqueue (idempotent)
    def enqueue_from_approval(self, approval_task_id: str) -> str | None:
        """Materialise an approved task into exactly one outbound row. Re-running is a no-op."""
        with db.tx(self.s.database_url) as conn:
            row = conn.execute(
                """SELECT a.status, a.final_subject, a.final_body, d.contact_id, d.org_id, d.thread_id, d.kind,
                          d.inbound_message_id
                     FROM flightbridge.approval_task a JOIN flightbridge.ai_draft d ON d.id = a.draft_id
                    WHERE a.id = %s FOR UPDATE OF a""", (approval_task_id,)).fetchone()
            if row is None or row[0] not in APPROVED:
                return None
            status, subj, main_body, contact_id, org_id, thread_id, kind, inbound_id = row
            ref = new_ref_token()
            body = compose_body(main_body, self.profile, ref)
            out = conn.execute(
                """INSERT INTO flightbridge.outbound_message
                     (idempotency_key, contact_id, org_id, thread_id, approval_task_id, reply_to_message_id, kind,
                      subject, body_text, content_sha256, ref_token)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
                (f"approval:{approval_task_id}", contact_id, org_id, thread_id, approval_task_id, inbound_id, kind,
                 subj, body, content_sha256(subj, body), ref)).fetchone()
            if out is None:
                existing = conn.execute("SELECT id FROM flightbridge.outbound_message WHERE idempotency_key=%s",
                                        (f"approval:{approval_task_id}",)).fetchone()
                return str(existing[0]) if existing else None
            db.audit(conn, "outbound_enqueued", "outbound_message", out[0], {"approval_task_id": str(approval_task_id)})
            return str(out[0])

    # ------------------------------------------------------------ helpers
    def _suppression_for(self, conn, email: str, org_id) -> SuppressionIndex:
        rows = conn.execute(
            """SELECT scope, email_hash, org_id::text, domain FROM flightbridge.suppression
                WHERE (scope='contact' AND email_hash=%s) OR (scope='organisation' AND org_id=%s)
                   OR (scope='domain' AND domain=%s)""",
            (email_hash(email), org_id, email_domain(email))).fetchall()
        public = [r[0] for r in conn.execute("SELECT domain FROM flightbridge.public_mail_domain").fetchall()]
        idx = SuppressionIndex(public_domains=public)
        for scope, h, o, d in rows:
            if scope == "contact":
                idx.contacts.add(h)
            elif scope == "organisation":
                idx.orgs.add(o)
            elif d not in idx.public:
                idx.domains.add(d)
        return idx

    def _evidence_for(self, conn, contact_id) -> list[Evidence]:
        rows = conn.execute(
            """SELECT fact, source_url, excerpt, method, verified_at, verified_by::text
                 FROM flightbridge.contact_fact_evidence WHERE contact_id=%s AND expires_at > now()""",
            (contact_id,)).fetchall()
        return [Evidence(*r) for r in rows]

    def _record_check(self, conn, outbound_id, kind, decision, rule_key, reasons, sha=None) -> int:
        return conn.execute(
            """INSERT INTO flightbridge.compliance_check
                 (outbound_id, kind, decision, rule_key, rules_version, content_sha256, reasons)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (outbound_id, kind, decision, rule_key, self.rules["_meta"]["version"], sha, json.dumps(reasons))).fetchone()[0]

    def _kill_switch_engaged(self, conn) -> bool:
        row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='outbound_kill_switch'").fetchone()
        return row is None or row[0] is not False   # missing or anything but JSON false => engaged

    def _get_transport(self):
        if self._transport is None:
            if self._transport_factory is None:
                from .transports import HostingerMailApiTransport
                self._transport = HostingerMailApiTransport(
                    base_url=self.s.hostinger_base_url, token=self.s.hostinger_token,
                    mailbox_id=self.s.hostinger_mailbox_id, capability=_SEND_CAPABILITY)
            else:
                self._transport = self._transport_factory(capability=_SEND_CAPABILITY)
        return self._transport

    # ------------------------------------------------------------ authorise + send
    def process(self, outbound_id: str) -> ProcessOutcome:
        reasons: list = []
        with db.tx(self.s.database_url) as conn:
            row = conn.execute(
                """SELECT o.status, o.subject, o.body_text, o.kind, o.ref_token, o.content_sha256, o.approval_task_id,
                          a.status, a.final_subject, a.final_body, c.email, c.is_role_address, c.id, g.id, g.country
                     FROM flightbridge.outbound_message o
                     JOIN flightbridge.contact c ON c.id = o.contact_id
                     JOIN flightbridge.organisation g ON g.id = o.org_id
                LEFT JOIN flightbridge.approval_task a ON a.id = o.approval_task_id
                    WHERE o.id = %s FOR UPDATE OF o""", (outbound_id,)).fetchone()
            if row is None:
                return ProcessOutcome(outbound_id, "missing", ["not_found"])
            (status, subject, body, kind, ref, sha, appr_id, appr_status, appr_subject, appr_body,
             email, is_role, contact_id, org_id, country) = row
            if status != "pending_checks":
                return ProcessOutcome(outbound_id, status, ["already_processed"])   # idempotent re-run

            if (appr_id is None or appr_status not in APPROVED or appr_subject != subject
                    or not body.startswith((appr_body or '\x00').rstrip())):
                reasons.append("human_approval_missing_or_mismatched")

            ctx = RecipientContext(email=email, country=country, is_role_address=is_role, org_id=str(org_id),
                                   evidence=self._evidence_for(conn, contact_id))
            j = jurisdiction_gate.evaluate(ctx, self.rules, self._suppression_for(conn, email, org_id))
            j_reasons = j.reasons + reasons
            j_decision = j.decision if not reasons else "BLOCK"
            e_id = self._record_check(conn, outbound_id, "jurisdiction", j_decision, j.rule_key, j_reasons)

            c = content_gate.evaluate(OutboundContent(subject, body, kind, ref), j.content_requirements, self.profile)
            if c.content_sha256 != sha:
                c.failures.append("content_hash_mismatch")
                c.decision = "FAIL"
            c_id = self._record_check(conn, outbound_id, "content", c.decision, None, c.failures, c.content_sha256)

            if j_decision == "ALLOW" and c.decision == "PASS":
                new = "authorized"
            elif j_decision == "HUMAN_REVIEW" and c.decision == "PASS" and not reasons:
                new = "needs_review"
            else:
                new = "blocked"
            conn.execute(
                """UPDATE flightbridge.outbound_message
                      SET status=%s, eligibility_check_id=%s, content_check_id=%s,
                          authorized_at = CASE WHEN %s='authorized' THEN now() ELSE NULL END
                    WHERE id=%s""", (new, e_id, c_id, new, outbound_id))
            all_reasons = j_reasons + c.failures
            db.audit(conn, f"outbound_{new}", "outbound_message", outbound_id,
                     {"jurisdiction": j_decision, "content": c.decision, "reasons": all_reasons})
            if new != "authorized":
                return ProcessOutcome(outbound_id, new, all_reasons)

        with db.tx(self.s.database_url) as conn:
            if not self.s.may_transmit or self._kill_switch_engaged(conn):
                why = ("DRY_RUN" if self.s.dry_run else "OUTBOUND_ENABLED=false" if not self.s.outbound_enabled
                       else "kill_switch")
                conn.execute("UPDATE flightbridge.outbound_message SET status='dry_run_rendered' "
                             "WHERE id=%s AND status='authorized'", (outbound_id,))
                db.audit(conn, "outbound_dry_run_rendered", "outbound_message", outbound_id, {"why": why})
                return ProcessOutcome(outbound_id, "dry_run_rendered", [why])
            claimed = conn.execute(
                "UPDATE flightbridge.outbound_message SET status='sending', attempts=attempts+1 "
                "WHERE id=%s AND status='authorized' RETURNING id", (outbound_id,)).fetchone()
            if not claimed:
                return ProcessOutcome(outbound_id, "not_claimed", ["concurrent_claim"])

        from .transports import DefiniteRejection, SendRequest, TransportError
        try:
            self._get_transport().send(SendRequest(to=email, subject=subject, text=body,
                                                   display_name=self.s.sender_display_name))
            final, err = "sent", None
        except DefiniteRejection as exc:
            final, err = "failed", str(exc)
        except (TransportError, Exception) as exc:   # anything else: we cannot know if it went out
            final, err = "send_unknown", f"{type(exc).__name__}:{exc}"
        with db.tx(self.s.database_url) as conn:
            conn.execute(
                "UPDATE flightbridge.outbound_message SET status=%s, last_error=%s, "
                "sent_at = CASE WHEN %s='sent' THEN now() ELSE NULL END WHERE id=%s AND status='sending'",
                (final, err, final, outbound_id))
            db.audit(conn, f"outbound_{final}", "outbound_message", outbound_id, {"error": err})
        return ProcessOutcome(outbound_id, final, [err] if err else [])

    def process_ready(self, limit: int = 20) -> list[ProcessOutcome]:
        """Materialise approved tasks, then process pending outbound rows. Safe to run repeatedly."""
        with db.tx(self.s.database_url) as conn:
            tasks = [r[0] for r in conn.execute(
                """SELECT a.id FROM flightbridge.approval_task a
                    WHERE a.status IN ('approved','edited_approved')
                      AND NOT EXISTS (SELECT 1 FROM flightbridge.outbound_message o
                                       WHERE o.idempotency_key = 'approval:' || a.id::text)
                    ORDER BY a.decided_at LIMIT %s""", (limit,)).fetchall()]
        for t in tasks:
            self.enqueue_from_approval(str(t))
        with db.tx(self.s.database_url) as conn:
            ids = [str(r[0]) for r in conn.execute(
                "SELECT id FROM flightbridge.outbound_message WHERE status='pending_checks' ORDER BY created_at LIMIT %s",
                (limit,)).fetchall()]
        return [self.process(i) for i in ids]
