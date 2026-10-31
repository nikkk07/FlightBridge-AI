"""One processing cycle; safe to run repeatedly (cron, launchd on the Mac Mini, or a loop).

  1. read new inbound mail (if INBOUND_SOURCE is set) -> store -> classify -> effects -> drafts + approval tasks
  2. due follow-ups -> drafts + approval tasks
  3. approved tasks -> central sending service (gates; DRY_RUN => rendered only)
Every step is idempotent, so an interrupted cycle simply resumes on the next run.
"""
from __future__ import annotations

import logging
import time

from . import db, followups
from .ai.llm import groq_classifier
from .compliance.rules import load_rules
from .inbound.ingest import poll_and_process
from .inbound.readers import HostingerApiReader, ImapReader
from .sending.service import SendingService
from .settings import Settings, load_sender_profile

log = logging.getLogger("flightbridge.worker")


def build_reader(s: Settings):
    if s.inbound_source == "hostinger":
        if not (s.hostinger_token and s.hostinger_mailbox_id):
            raise ValueError("INBOUND_SOURCE=hostinger needs HOSTINGER_MAIL_API_TOKEN and HOSTINGER_MAILBOX_RESOURCE_ID")
        return HostingerApiReader(s.hostinger_base_url, s.hostinger_token, s.hostinger_mailbox_id)
    if s.inbound_source == "imap":
        if not (s.imap_host and s.imap_user and s.imap_password):
            raise ValueError("INBOUND_SOURCE=imap needs IMAP_HOST, IMAP_USER and IMAP_PASSWORD")
        return ImapReader(s.imap_host, s.imap_port, s.imap_user, s.imap_password)
    return None


def run_once(s: Settings, reader=None, sender: SendingService | None = None) -> dict:
    out = {"dry_run": s.dry_run, "outbound_enabled": s.outbound_enabled}
    reader = reader if reader is not None else build_reader(s)
    if reader is not None:
        try:
            out["inbound"] = poll_and_process(s.database_url, reader, llm=groq_classifier())
        except Exception as exc:             # inbox outage must not stop follow-ups or the send queue
            log.exception("inbound polling failed")
            out["inbound_error"] = f"{type(exc).__name__}: {exc}"
        with db.tx(s.database_url) as conn:
            conn.execute("UPDATE flightbridge.webhook_event SET processed_at=now() WHERE processed_at IS NULL")
    out["followups"] = followups.process_due(s.database_url)
    sender = sender or SendingService(s, load_rules(), load_sender_profile())
    out["outbound"] = [o.__dict__ for o in sender.process_ready()]
    return out


def loop(s: Settings, interval: int = 300) -> None:
    while True:
        try:
            log.info("cycle: %s", run_once(s))
        except Exception:
            log.exception("cycle failed; retrying next interval")
        time.sleep(max(60, interval))
