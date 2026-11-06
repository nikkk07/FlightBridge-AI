"""One processing cycle; safe to run repeatedly (GitHub Actions cron, launchd on the Mac, or a loop).

  0. "Run now" requests from the app, then the research autopilot (automation.py): find websites -> crawl ->
     qualify -> draft from the approved template into the approval queue
  1. read new inbound mail (if INBOUND_SOURCE is set) -> store -> classify -> effects -> drafts + approval tasks
  2. due follow-ups -> drafts + approval tasks
  3. approved tasks -> central sending service (gates; DRY_RUN => rendered only)
Every step is idempotent, so an interrupted cycle simply resumes on the next run.
"""
from __future__ import annotations

import logging
import json
import time

from . import automation, db, followups, secrets, verify
from .ai.llm import groq_classifier
from .compliance.rules import load_rules
from .inbound.ingest import poll_and_process
from .inbound.readers import HostingerApiReader, ImapReader
from .research import quota
from .research.fetch import SafeFetcher
from .sending.service import SendingService
from .settings import Settings, load_sender_profile

log = logging.getLogger("flightbridge.worker")


def build_reader(s: Settings):
    source = s.inbound_source or (secrets.settings().get("inbound_source") if s.database_url else None)
    if source == "hiox":                # HIOX India mailbox (cPanel): IMAP over TLS on 993
        cfg = secrets.settings()
        password = secrets.get("hiox_mail_password")
        if not password:
            raise ValueError("Reading replies needs the HIOX India mailbox password: save it in FlightBridge Settings")
        return ImapReader(cfg["hiox_mail_host"], 993, cfg["hiox_mail_user"], password)
    if source == "hostinger":
        token = s.hostinger_token or secrets.get("hostinger_mail_api_token")
        mailbox = s.hostinger_mailbox_id or secrets.get("hostinger_mailbox_resource_id")
        if not (token and mailbox):
            raise ValueError("Reading replies needs the Hostinger API token and mailbox ID: save them in FlightBridge Settings "
                             "(or set HOSTINGER_MAIL_API_TOKEN and HOSTINGER_MAILBOX_RESOURCE_ID)")
        return HostingerApiReader(s.hostinger_base_url, token, mailbox)
    if s.inbound_source == "imap":
        if not (s.imap_host and s.imap_user and s.imap_password):
            raise ValueError("INBOUND_SOURCE=imap needs IMAP_HOST, IMAP_USER and IMAP_PASSWORD")
        return ImapReader(s.imap_host, s.imap_port, s.imap_user, s.imap_password)
    return None


def run_once(s: Settings, reader=None, sender: SendingService | None = None, fetcher=None,
             research: bool = True, trigger: str = "schedule") -> dict:
    secrets.configure(s.database_url)
    db.ensure_schema(s.database_url)          # installs new FlightBridge tables/columns after an upgrade
    quota.install(s.database_url)             # every Google Places call counted; refused at the free monthly cap
    if not automation.acquire_engine(s.database_url):
        return {"skipped": "another FlightBridge cycle is running"}
    try:
        return _run_cycle(s, reader, sender, fetcher, research, trigger)
    finally:
        automation.release_engine(s.database_url)


def _run_cycle(s: Settings, reader, sender, fetcher, research: bool, trigger: str) -> dict:
    run_id = automation.start_run(s.database_url, trigger)
    out = {"run_id": run_id, "dry_run": s.dry_run, "outbound_enabled": s.outbound_enabled}
    try:
        if research:
            fetcher = fetcher or SafeFetcher(s.crawler_user_agent)
            out["jobs"] = automation.process_jobs(s.database_url, fetcher)
            out["research"] = automation.research_cycle(s.database_url, fetcher)
        reader = reader if reader is not None else build_reader(s)
        if reader is not None:
            try:
                out["inbound"] = poll_and_process(s.database_url, reader, llm=groq_classifier())
            except Exception as exc:             # inbox outage must not stop follow-ups or the send queue
                log.exception("inbound polling failed")
                out["inbound_error"] = f"{type(exc).__name__}: {exc}"
            with db.tx(s.database_url) as conn:
                conn.execute("UPDATE flightbridge.webhook_event SET processed_at=now() WHERE processed_at IS NULL")
        else:
            out["inbound"] = "not configured"
        out["followups"] = followups.process_due(s.database_url)
        sender = sender or SendingService(s, load_rules(), load_sender_profile())
        out["outbound"] = [o.__dict__ for o in sender.process_ready()]
    except Exception as exc:
        automation.finish_run(s.database_url, run_id, out, f"{type(exc).__name__}: {exc}"[:1000])
        raise
    stats = dict(out)
    stats.update({k: v for k, v in (out.get("research") or {}).items() if k.endswith("_error")})
    automation.finish_run(s.database_url, run_id, stats)
    return out


def verify_step(s: Settings, fetcher=None) -> dict:
    """One step of the 24x7 Verify worker (flightbridge/verify.py), run between cycles. Honours the CRM's
    "Run automation" switch and the Automation > verify setting. {'idle': True} means nothing is waiting."""
    secrets.configure(s.database_url)
    db.ensure_schema(s.database_url)
    quota.install(s.database_url)
    cfg = automation.autopilot(s.database_url)
    if not cfg.get("verify", True) or not cfg.get("eligibility", True):
        return {"idle": True, "off": True}
    return verify.step(s.database_url, fetcher or SafeFetcher(s.crawler_user_agent))


def autosend_step(s: Settings, sender: SendingService | None = None, dns=None) -> dict:
    """One step of Email Auto-Pilot (flightbridge/autosend.py): at most one email, when everything allows it.
    Run it from the computer that can reach the mail server (the Mac Mini); it is a no-op while Auto-Pilot is off."""
    from . import autosend
    secrets.configure(s.database_url)
    db.ensure_schema(s.database_url)
    return autosend.step(s, sender=sender, dns=dns)


MAC_FRESH = 180          # seconds: the Mac reported this recently, so it is the one sending


def cloud_send(s: Settings, minutes: float = 9.0, every: int = 30, clock=time.monotonic, sleep=time.sleep,
               sender: SendingService | None = None, reader=None, dns=None) -> dict:
    """Email Auto-Pilot from the cloud (GitHub Actions), for when the Mac Mini is off.
    Does nothing while the Mac reports in (it sends then). Otherwise: reads replies and makes due follow-ups first
    (so a school that answered never gets a follow-up), then takes an Auto-Pilot step every `every` seconds for
    `minutes`. The same claim, gates, kill switch and pace apply, so the Mac and the cloud can never double-send.
    Returns only counts and state names (the run log of a public repository is public)."""
    from datetime import datetime, timezone
    from . import autosend
    secrets.configure(s.database_url)
    db.ensure_schema(s.database_url)
    with db.tx(s.database_url) as conn:
        row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='engine_status'").fetchone()
        enabled = autosend.config(conn)["enabled"]
    env = (row[0] if row else None) or {}
    try:
        mac_at = datetime.fromisoformat(env["at"]) if env.get("at") else None
    except ValueError:
        mac_at = None
    now = datetime.now(timezone.utc)

    def beat(state: str, **kw) -> dict:
        with db.tx(s.database_url) as conn:
            conn.execute("""INSERT INTO flightbridge.system_setting (key, value, updated_at) VALUES ('cloud_sender', %s::jsonb, now())
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                         (json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "state": state,
                                      "may_send": s.may_transmit, **kw}),))
        return {"state": state, **kw}

    if mac_at and (now - mac_at).total_seconds() < MAC_FRESH and env.get("runner") != "github-actions":
        return beat("mac_online")
    if not enabled:
        return beat("autopilot_off")
    try:
        first = run_once(s, reader=reader, sender=sender, research=False, trigger="schedule")
    except Exception as exc:                   # reported by type only; the step loop below still honours every gate
        first = {"error": type(exc).__name__}
    states: dict[str, int] = {"cycle_skipped": 1} if first.get("skipped") else {}   # autosend has its own claim
    end = clock() + minutes * 60
    while True:
        try:
            st = autosend_step(s, sender=sender, dns=dns).get("state", "?")
        except Exception as exc:               # keep going; report the type only
            st = f"error:{type(exc).__name__}"
        states[st] = states.get(st, 0) + 1
        if st in ("paused", "off", "blocked") or clock() + every > end:
            break
        sleep(every)
    return beat("ran", steps=states, sent=states.get("sent", 0))


def loop(s: Settings, interval: int = 900, poll: int = 60) -> None:
    """Full cycle every `interval` seconds; in between, picks up "Run now" requests within `poll` seconds."""
    last = 0.0
    while True:
        try:
            if time.time() - last >= interval:
                log.info("cycle: %s", run_once(s))
                last = time.time()
            elif automation.queued_jobs(s.database_url):
                log.info("run-now: %s", run_once(s, trigger="queue"))
            elif not verify_step(s).get("idle"):
                continue                       # more schools waiting: next step straight away
        except Exception:
            log.exception("cycle failed; retrying")
        time.sleep(max(15, poll))
