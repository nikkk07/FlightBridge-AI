"""Autopilot: the research half of the worker cycle, plus the run log and the "Run now" queue.

Each cycle (schedule or Run-now) does, in order and within the batch size:
  1. find websites   — Google Places candidate, confirmed on the school's own homepage (research/places.py)
  2. research        — robots-aware crawl of the school's site: public emails + website signals (+ free-LLM summary)
  3. qualify         — explainable score; never auto-rejects a regulator-approved school
  4. draft           — only from the owner's APPROVED template, always into the approval queue
Mail reading, follow-ups and the sending service run after this in worker.run_once. Nothing here sends email.
Every stage is idempotent and bounded, so an interrupted run simply continues next time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
from pathlib import Path

from . import db
from .research import pipeline
from .research.fetch import SafeFetcher
from . import eligibility
from .research.discover import assign_areas, due_areas, search_area
from .research.places import PlacesError, find_website

log = logging.getLogger("flightbridge.automation")
DEFAULT_AUTOPILOT = {"discover_areas": True, "find_websites": True, "research": True, "eligibility": True,
                     "qualify": True, "draft": True, "batch_size": 8, "areas_per_cycle": 2}
OPEN_STATUSES = ("discovered", "research_pending")
WEBSITE_RETRY_DAYS = 30


def runner_name() -> str:
    return os.environ.get("FB_RUNNER") or f"manual@{socket.gethostname()}"


def autopilot(database_url: str) -> dict:
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='autopilot'").fetchone()
    cfg = dict(DEFAULT_AUTOPILOT)
    if row and isinstance(row[0], dict):
        cfg.update({k: v for k, v in row[0].items() if k in DEFAULT_AUTOPILOT})
    cfg["batch_size"] = max(1, min(int(cfg.get("batch_size") or 8), 50))
    cfg["areas_per_cycle"] = max(0, min(int(cfg.get("areas_per_cycle") or 0), 10))
    return cfg


# ---------------------------------------------------------------- engine lock (one cycle at a time, anywhere)
ENGINE_LEASE_SECONDS = 900   # longer than a normal cycle; a crashed runner frees it within 15 minutes


def _holder() -> str:
    """Unique per process, so two runners with the same name (two GitHub runs) can't share the lock."""
    return f"{runner_name()}|{socket.gethostname()}:{os.getpid()}"


def acquire_engine(database_url: str, seconds: int = ENGINE_LEASE_SECONDS) -> bool:
    """Atomic single-statement lease (pgbouncer-safe). Missing table (migration 004 not applied) => no lock, proceed."""
    try:
        with db.tx(database_url) as conn:
            row = conn.execute("""
                INSERT INTO flightbridge.engine_lease (name, holder, lease_until)
                VALUES ('engine', %s, now() + make_interval(secs => %s))
                ON CONFLICT (name) DO UPDATE SET holder=EXCLUDED.holder, lease_until=EXCLUDED.lease_until
                    WHERE flightbridge.engine_lease.lease_until < now() OR flightbridge.engine_lease.holder = EXCLUDED.holder
                RETURNING name""", (_holder(), seconds)).fetchone()
        return row is not None
    except Exception as exc:
        if "engine_lease" in str(exc):
            return True
        raise


def release_engine(database_url: str) -> None:
    try:
        with db.tx(database_url) as conn:
            conn.execute("UPDATE flightbridge.engine_lease SET lease_until=now() WHERE name='engine' AND holder=%s", (_holder(),))
    except Exception:
        pass


# ---------------------------------------------------------------- run log
def start_run(database_url: str, trigger: str = "schedule") -> int:
    with db.tx(database_url) as conn:
        # A run that died without finishing (container killed) is closed as failed after 2 hours.
        conn.execute("""UPDATE flightbridge.worker_run SET status='failed', finished_at=now(), error='did not finish'
                        WHERE status='running' AND started_at < now() - interval '2 hours'""")
        return conn.execute("INSERT INTO flightbridge.worker_run (runner, trigger) VALUES (%s,%s) RETURNING id",
                            (runner_name(), trigger)).fetchone()[0]


def finish_run(database_url: str, run_id: int, stats: dict, error: str | None = None) -> None:
    status = "failed" if error else ("partial" if any(k.endswith("_error") for k in stats) else "ok")
    with db.tx(database_url) as conn:
        conn.execute("UPDATE flightbridge.worker_run SET finished_at=now(), status=%s, stats=%s, error=%s WHERE id=%s",
                     (status, json.dumps(stats, default=str), error, run_id))


# ---------------------------------------------------------------- register sync
REGISTER_DIR = Path(__file__).resolve().parent / "registers"   # shipped inside the package


def sync_registers(database_url: str, root: Path | None = None) -> dict:
    """Import every data/registers/**/*_register.csv whose content changed since the last import.
    The sha256 guard matters: re-importing would refresh checked_at and falsely claim a new regulator check."""
    from .research.registers import read_csv
    root = root or REGISTER_DIR
    out = {"files_imported": 0, "created": 0, "matched": 0, "rejected_rows": 0}
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='register_files'").fetchone()
    seen = dict(row[0]) if row and isinstance(row[0], dict) else {}
    for f in sorted(root.glob("**/*_register.csv")):
        key = f.relative_to(root).as_posix()
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        if seen.get(key) == digest:
            continue
        rows, bad = read_csv(str(f))
        stats = pipeline.import_rows(database_url, rows)
        seen[key] = digest
        with db.tx(database_url) as conn:
            conn.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('register_files', %s::jsonb)
                            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (json.dumps(seen),))
            db.audit(conn, "register_imported", "register", key, {**stats, "sha256": digest, "rejected_rows": len(bad)})
        out["files_imported"] += 1
        out["created"] += stats["created"]
        out["matched"] += stats["matched"]
        out["rejected_rows"] += len(bad)
    return out


# ---------------------------------------------------------------- stages
def _record_website(conn, org_id, found: dict) -> None:
    owner = conn.execute("SELECT id FROM flightbridge.organisation WHERE lower(website_domain)=%s AND id<>%s",
                         (found["domain"].lower(), org_id)).fetchone()
    if owner:     # branches of one company share a website: keep it on the first school, note it on this one
        db.audit(conn, "website_shared", "organisation", org_id, {"domain": found["domain"], "owner": str(owner[0])})
        return
    conn.execute("UPDATE flightbridge.organisation SET website_domain=%s, updated_at=now() WHERE id=%s AND website_domain IS NULL",
                 (found["domain"], org_id))
    conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                    VALUES (%s,'domain',%s,%s) ON CONFLICT (scheme, value) DO NOTHING""", (org_id, found["domain"], found["url"]))
    if found.get("place_id"):
        conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                        VALUES (%s,'google_place_id',%s,NULL) ON CONFLICT (scheme, value) DO NOTHING""", (org_id, found["place_id"]))


def find_websites(database_url: str, fetcher: SafeFetcher, limit: int, org_id: str | None = None) -> dict:
    out = {"searched": 0, "found": 0, "not_found": 0}
    with db.tx(database_url) as conn:
        if org_id:
            rows = conn.execute("""SELECT o.id, o.name, o.city, o.country, ra.approval_ref FROM flightbridge.organisation o
                LEFT JOIN flightbridge.regulatory_approval ra ON ra.org_id=o.id WHERE o.id=%s AND o.website_domain IS NULL""",
                                (org_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT o.id, o.name, o.city, o.country, ra.approval_ref FROM flightbridge.organisation o
                LEFT JOIN LATERAL (SELECT approval_ref FROM flightbridge.regulatory_approval r WHERE r.org_id=o.id
                                   ORDER BY checked_at DESC LIMIT 1) ra ON true
                WHERE o.deleted_at IS NULL AND o.website_domain IS NULL AND o.status = ANY(%s)
                  AND NOT EXISTS (SELECT 1 FROM flightbridge.audit_log a WHERE a.entity='organisation' AND a.entity_id=o.id::text
                                  AND a.action='website_search' AND a.at > now() - %s * interval '1 day')
                ORDER BY o.discovered_at LIMIT %s""", (list(OPEN_STATUSES), WEBSITE_RETRY_DAYS, limit)).fetchall()
    for oid, name, city, country, ref in rows:
        res = find_website(name, city, country, ref, fetcher)     # PlacesError propagates: stop the stage, report it
        out["searched"] += 1
        with db.tx(database_url) as conn:
            if res["status"] == "found":
                _record_website(conn, oid, res)
                out["found"] += 1
            else:
                out["not_found"] += 1
            db.audit(conn, "website_search", "organisation", oid,
                     {k: v for k, v in res.items() if k in ("status", "url", "domain", "matched_tokens", "approval_ref_on_site")})
    return out


def research(database_url: str, fetcher: SafeFetcher, limit: int, org_id: str | None = None) -> dict:
    out = {"researched": 0, "emails_found": 0}
    with db.tx(database_url) as conn:
        if org_id:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM flightbridge.organisation WHERE id=%s AND website_domain IS NOT NULL", (org_id,)).fetchall()]
        else:
            ids = [r[0] for r in conn.execute(
                """SELECT id FROM flightbridge.organisation WHERE deleted_at IS NULL AND website_domain IS NOT NULL
                   AND status = ANY(%s) ORDER BY updated_at LIMIT %s""", (list(OPEN_STATUSES), limit)).fetchall()]
    for oid in ids:
        r = pipeline.research_org(database_url, str(oid), fetcher)
        out["researched"] += 1
        out["emails_found"] += r.get("contacts", 0)
    return out


def qualify(database_url: str, limit: int, org_id: str | None = None) -> dict:
    with db.tx(database_url) as conn:
        if org_id:
            ids = [org_id]
        else:
            ids = [r[0] for r in conn.execute(
                """SELECT o.id FROM flightbridge.organisation o WHERE o.deleted_at IS NULL AND o.status='research_completed'
                   AND o.eligibility <> 'unqualified' AND o.org_kind <> 'ground_school'
                   ORDER BY o.updated_at LIMIT %s""", (limit,)).fetchall()]
    out = {"qualified": 0, "needs_review": 0}
    for oid in ids:
        q = pipeline.qualify_org(database_url, str(oid))
        out["qualified" if q["recommended_action"] == "draft_outreach_for_approval" else "needs_review"] += 1
    return out


def draft(database_url: str, limit: int, org_id: str | None = None) -> dict:
    with db.tx(database_url) as conn:
        ids = [org_id] if org_id else [r[0] for r in conn.execute(
            """SELECT o.id FROM flightbridge.organisation o WHERE o.deleted_at IS NULL AND o.status='qualified'
               AND o.eligibility = 'eligible_verified'
               AND NOT EXISTS (SELECT 1 FROM flightbridge.ai_draft d WHERE d.org_id=o.id AND d.kind='initial')
               ORDER BY o.updated_at LIMIT %s""", (limit,)).fetchall()]
    made = sum(1 for oid in ids if pipeline.draft_initial(database_url, str(oid)))
    return {"drafts_created": made}


def _stage(out: dict, name: str, fn, *args, **kw) -> None:
    try:
        out[name] = fn(*args, **kw)
    except PlacesError as exc:
        out[f"{name}_error"] = str(exc)
    except Exception as exc:          # one stage failing never stops the others
        log.exception("stage %s failed", name)
        out[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:500]


def research_cycle(database_url: str, fetcher: SafeFetcher, cfg: dict | None = None) -> dict:
    cfg = cfg or autopilot(database_url)
    n, out = cfg["batch_size"], {}
    _stage(out, "registers", sync_registers, database_url)
    _stage(out, "cricos", sync_cricos, database_url)
    if cfg["discover_areas"] and cfg["areas_per_cycle"]:
        _stage(out, "discover", discover_areas, database_url, cfg["areas_per_cycle"])
    _stage(out, "assign_areas", lambda u: {"assigned": assign_areas(u)}, database_url)
    if cfg["find_websites"]:
        _stage(out, "find_websites", find_websites, database_url, fetcher, n)
    if cfg["research"]:
        _stage(out, "research", research, database_url, fetcher, n)
    if cfg["eligibility"]:
        _stage(out, "eligibility", eligibility.run, database_url, n * 4)
    if cfg["qualify"]:
        _stage(out, "qualify", qualify, database_url, n * 2)
    if cfg["draft"]:
        _stage(out, "draft", draft, database_url, n * 2)
    return out


def discover_areas(database_url: str, limit: int, area_id: int | None = None) -> dict:
    ids = [area_id] if area_id else due_areas(database_url, limit)
    out = {"areas": 0, "new": 0, "matched": 0, "phones": 0}
    for aid in ids:
        r = search_area(database_url, aid)
        out["areas"] += 1
        for k in ("new", "matched", "phones"):
            out[k] += r.get(k, 0)
    return out


CRICOS_CHECK_HOURS = 24


def sync_cricos(database_url: str, session=None) -> dict:
    """Australia's CRICOS export (monthly). Checked at most once a day; imported only when a new file appears."""
    from .research.cricos import latest_resource, parse
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT value, updated_at > now() - make_interval(hours => %s) FROM flightbridge.system_setting "
                           "WHERE key='cricos_sync'", (CRICOS_CHECK_HOURS,)).fetchone()
    state = dict(row[0]) if row and isinstance(row[0], dict) else {}
    if row and row[1]:
        return {"skipped": "checked in the last 24 h", "version": state.get("version")}
    import requests
    res = latest_resource(session)
    if state.get("resource_id") == res["id"]:
        out = {"skipped": "no new CRICOS file", "version": state.get("version")}
    else:
        r = (session or requests).get(res["url"], timeout=120)
        r.raise_for_status()
        version = f"CRICOS export, {res.get('name') or res.get('created', '')[:10]}"
        rows = parse(r.content, res["url"], version)
        stats = pipeline.import_rows(database_url, rows)
        state = {"resource_id": res["id"], "version": version, "providers": len(rows)}
        out = {"imported": len(rows), **stats, "version": version}
        with db.tx(database_url) as conn:
            db.audit(conn, "register_imported", "register", "AU/CRICOS", out)
    with db.tx(database_url) as conn:
        conn.execute("""INSERT INTO flightbridge.system_setting (key, value, updated_at) VALUES ('cricos_sync', %s::jsonb, now())
                        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (json.dumps(state),))
    return out


# ---------------------------------------------------------------- Run-now queue
def claim_jobs(database_url: str, limit: int = 10) -> list[tuple]:
    with db.tx(database_url) as conn:
        return conn.execute("""
            UPDATE flightbridge.job_request SET status='running', started_at=now()
             WHERE id IN (SELECT id FROM flightbridge.job_request WHERE status='queued'
                          ORDER BY requested_at LIMIT %s FOR UPDATE SKIP LOCKED)
            RETURNING id, kind, org_id, area_id""", (limit,)).fetchall()


def run_job(database_url: str, fetcher: SafeFetcher, kind: str, org_id, area_id=None) -> dict:
    cfg = autopilot(database_url)
    out: dict = {}
    oid = str(org_id) if org_id else None
    if kind == "search_area":
        _stage(out, "discover", discover_areas, database_url, 1, area_id)
        _stage(out, "find_websites", find_websites, database_url, fetcher, cfg["batch_size"])
        _stage(out, "research", research, database_url, fetcher, cfg["batch_size"])
        _stage(out, "eligibility", eligibility.run, database_url, cfg["batch_size"] * 4)
    if kind == "research_school":
        _stage(out, "find_websites", find_websites, database_url, fetcher, 1, oid)
        _stage(out, "research", research, database_url, fetcher, 1, oid)
        _stage(out, "eligibility", eligibility.run, database_url, 1, oid)
        _stage(out, "qualify", qualify, database_url, 1, oid)
        _stage(out, "draft", draft, database_url, 1, oid)
    elif kind == "find_websites":
        _stage(out, "find_websites", find_websites, database_url, fetcher, cfg["batch_size"] * 3)
    elif kind == "research_batch":
        _stage(out, "research", research, database_url, fetcher, cfg["batch_size"] * 2)
        _stage(out, "eligibility", eligibility.run, database_url, cfg["batch_size"] * 8)
        _stage(out, "qualify", qualify, database_url, cfg["batch_size"] * 4)
        _stage(out, "draft", draft, database_url, cfg["batch_size"] * 4)
    # kind == "cycle": the normal cycle that follows covers it
    return out


def process_jobs(database_url: str, fetcher: SafeFetcher) -> list[dict]:
    done = []
    for jid, kind, org_id, area_id in claim_jobs(database_url):
        result = run_job(database_url, fetcher, kind, org_id, area_id)
        failed = any(k.endswith("_error") for k in result) and not any(not k.endswith("_error") for k in result)
        with db.tx(database_url) as conn:
            conn.execute("UPDATE flightbridge.job_request SET status=%s, finished_at=now(), result=%s WHERE id=%s",
                         ("failed" if failed else "done", json.dumps(result, default=str), jid))
        done.append({"id": jid, "kind": kind, **result})
    return done


def queued_jobs(database_url: str) -> int:
    with db.tx(database_url) as conn:
        return conn.execute("SELECT count(*) FROM flightbridge.job_request WHERE status='queued'").fetchone()[0]

