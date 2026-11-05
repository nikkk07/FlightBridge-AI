"""Verify: the 24x7 background worker that takes every undecided school to Eligible or Not Qualified.

Runs between the 15-minute cycles on the Mac Mini and alongside them on the CRM server; both share one queue (each
school is claimed by one runner at a time). For each school waiting for a decision ("Not checked yet" or "Check
needed"), using the strongest evidence first:

  1. official lists      every school is compared with the loaded lists (FAA Part 141, SEVP, Transport Canada, CRICOS
                         ...), including listed schools under another (legal) name — research/listmatch.py
  2. its own website     found by address guess (free), then Tavily, then Google (both paced inside their free monthly
                         allowance), then read (robots-aware); the keyword reading flags credential claims
  3. AI reading          for a flagged claim only: is it really this school's credential (not "I-20" the interstate)?
                         and the company's legal name, to match the official list — ai/site_check.py
  4. decision            eligibility.evaluate(): Eligible / Not Qualified; "Check needed" only when the evidence
                         conflicts, can't be confirmed automatically, or no website exists by every method

A school that is waiting for something (the monthly search allowance, a list upload, a re-check) comes back on its
own schedule (organisation.verify_next_at). Nothing here sends email or changes a person's decision.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import timedelta

from . import db, eligibility
from .research import quota
from .research.fetch import SafeFetcher

log = logging.getLogger("flightbridge.verify")
BATCH = 6                     # schools per step, read side by side
CLAIM_MINUTES = 15
WEBSITE_RETRY_DAYS = 30
RECHECK = {"unknown_waiting": timedelta(hours=6), "unknown": timedelta(days=1), "eligible_claimed": timedelta(days=7),
           "error": timedelta(hours=1)}
METHODS = (("guess", "website_guess"), ("tavily", "website_tavily"), ("google", "website_search"))


def claim(database_url: str, n: int) -> list[tuple]:
    with db.tx(database_url) as conn:
        return conn.execute("""
            UPDATE flightbridge.organisation o SET verify_claim_until = now() + %s * interval '1 minute'
             WHERE o.id IN (SELECT id FROM flightbridge.organisation
                             WHERE deleted_at IS NULL AND org_kind <> 'ground_school' AND status <> 'rejected'
                               AND eligibility IN ('unknown', 'eligible_claimed')
                               AND (verify_next_at IS NULL OR verify_next_at <= now())
                               AND (verify_claim_until IS NULL OR verify_claim_until < now())
                             ORDER BY verify_next_at NULLS FIRST, (website_domain IS NOT NULL) DESC, discovered_at
                             LIMIT %s FOR UPDATE SKIP LOCKED)
            RETURNING o.id, o.name, o.city, o.region, o.country, o.website_domain""", (CLAIM_MINUTES, n)).fetchall()


class Budget:
    """Paid-per-call look-ups allowed in this step, shared by the threads (paced inside the free monthly allowance)."""

    def __init__(self, database_url: str, n: int):
        from . import automation, secrets
        from .research import webfind
        self.lock = threading.Lock()
        self.tavily_key = secrets.get("tavily_api_key", database_url)
        self.left = {"guess": 10 ** 6,
                     "tavily": automation._paced(database_url, webfind.SKU, n) if self.tavily_key else 0,
                     "google": automation._paced(database_url, "text_search_enterprise", n)
                     if secrets.get("google_places_api_key", database_url) else 0}

    def take(self, method: str) -> bool:
        with self.lock:
            if self.left[method] <= 0:
                return False
            self.left[method] -= 1
            return True

    def stop(self, method: str) -> None:
        with self.lock:
            self.left[method] = 0


def find_site(database_url: str, org: tuple, fetcher: SafeFetcher, budget: Budget) -> str:
    """-> 'found' | 'not_found' (every usable method tried) | 'waiting' (a method is waiting for its allowance)."""
    from .automation import _record_website
    from .research import webfind
    from .research.places import PlacesError, find_website
    oid, name, city, region, country, _ = org
    with db.tx(database_url) as conn:
        done = {r[0] for r in conn.execute(
            """SELECT DISTINCT action FROM flightbridge.audit_log WHERE entity='organisation' AND entity_id=%s
                AND action = ANY(%s) AND at > now() - %s * interval '1 day'""",
            (str(oid), [a for _, a in METHODS], WEBSITE_RETRY_DAYS)).fetchall()}
        ref = conn.execute("""SELECT approval_ref FROM flightbridge.regulatory_approval WHERE org_id=%s
                              ORDER BY checked_at DESC LIMIT 1""", (oid,)).fetchone()
    ref = ref[0] if ref else None
    waiting = False
    for method, action in METHODS:
        if action in done:
            continue
        if method == "tavily" and not budget.tavily_key:
            continue
        if not budget.take(method):
            waiting = True
            continue
        try:
            if method == "guess":
                res = webfind.guess(name, city, region, country, ref, fetcher)
            elif method == "tavily":
                quota.reserve(database_url, webfind.SKU)
                res = webfind.tavily(name, city, region, country, ref, fetcher, budget.tavily_key)
            else:
                res = find_website(name, city, country, ref, fetcher)
                res.setdefault("method", "google")
        except (quota.QuotaExceeded, PlacesError):
            budget.stop(method)
            waiting = True
            continue
        except Exception as exc:                 # search service down: try that method again later
            log.warning("%s look-up failed for %s: %s", method, oid, exc)
            budget.stop(method)
            waiting = True
            continue
        with db.tx(database_url) as conn:
            if res["status"] == "found":
                _record_website(conn, oid, res)
            db.audit(conn, action, "organisation", oid,
                     {k: v for k, v in res.items() if k in ("status", "method", "url", "domain", "matched_tokens",
                                                            "approval_ref_on_site", "place_on_site", "tried")})
        if res["status"] == "found":
            return "found"
    return "waiting" if waiting else "not_found"


def ai_check(database_url: str, oid, name: str, country: str, texts: list[str], evidence: list[dict]) -> dict | None:
    from .ai import site_check
    rule = eligibility.load_rules().get(country) or {}
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT signals FROM flightbridge.organisation_profile WHERE org_id=%s", (oid,)).fetchone()
    signals = (row[0] if row else None) or {}
    claims = [c for c in rule.get("claims") or [] if signals.get(c)]
    if not claims:
        return None
    res = site_check.check(name, texts, claims, evidence)
    if res:
        with db.tx(database_url) as conn:
            conn.execute("UPDATE flightbridge.organisation_profile SET ai=%s WHERE org_id=%s", (json.dumps(res), oid))
            db.audit(conn, "ai_site_check", "organisation", oid, res)
    return res


def process(database_url: str, org: tuple, fetcher: SafeFetcher, budget: Budget, ctx: dict) -> str:
    from .research import pipeline
    oid, name, city, region, country, domain = org
    search = None
    if not domain:
        search = find_site(database_url, org, fetcher, budget)
        with db.tx(database_url) as conn:
            domain = conn.execute("SELECT website_domain FROM flightbridge.organisation WHERE id=%s", (oid,)).fetchone()[0]
    texts: list[str] = []
    if domain:
        with db.tx(database_url) as conn:
            pages = conn.execute("SELECT count(*) FROM flightbridge.website_page WHERE org_id=%s AND http_status=200",
                                 (oid,)).fetchone()[0]
            ai_done = conn.execute("SELECT ai IS NOT NULL FROM flightbridge.organisation_profile WHERE org_id=%s",
                                   (oid,)).fetchone()
        if not pages or not (ai_done and ai_done[0]):
            r = pipeline.research_org(database_url, str(oid), fetcher, max_pages=10, keep_texts=True)
            texts = r.get("texts") or []
            if texts:
                ai_check(database_url, oid, name, country, texts, r.get("evidence_list") or [])
    with db.tx(database_url) as conn:
        decision = eligibility.evaluate_org(conn, oid, ctx, texts)
        wait = None
        if decision == "unknown":
            wait = RECHECK["unknown_waiting" if search == "waiting" else "unknown"]
        elif decision == "eligible_claimed":
            wait = RECHECK["eligible_claimed"]
        conn.execute("""UPDATE flightbridge.organisation SET verify_claim_until=NULL,
                               verify_next_at = CASE WHEN %s::interval IS NULL THEN NULL ELSE now() + %s::interval END
                         WHERE id=%s""", (wait, wait, oid))
    return decision


def step(database_url: str, fetcher: SafeFetcher, size: int = BATCH) -> dict:
    """One bounded step (about a minute): quick re-decisions from the official lists, then up to `size` schools taken
    as far as the evidence allows. Returns counts; {'idle': True} when there was nothing to do."""
    from .automation import _clone
    out = {"eligibility": eligibility.run(database_url, 150)}
    rows = claim(database_url, size)
    out.update({"schools": len(rows), "eligible_verified": 0, "eligible_claimed": 0, "unqualified": 0, "unknown": 0,
                "errors": 0})
    if not rows:
        out["idle"] = out["eligibility"]["checked"] == 0
        return out
    budget = Budget(database_url, size)
    with db.tx(database_url) as conn:
        ctx = {"lists_loaded": eligibility.lists_loaded(conn), "methods": eligibility.search_methods(database_url)}
    local = threading.local()

    def run(org):
        if not hasattr(local, "f"):
            local.f = _clone(fetcher) or fetcher
        try:
            return process(database_url, org, local.f, budget, ctx)
        except Exception as exc:               # one school failing never stops the others; it is tried again later
            log.warning("verify %s failed: %s", org[0], exc)
            with db.tx(database_url) as conn:
                conn.execute("""UPDATE flightbridge.organisation SET verify_claim_until=NULL, verify_next_at=now() + %s
                                WHERE id=%s""", (RECHECK["error"], org[0]))
            return "errors"
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(size, len(rows))) as pool:
        for d in pool.map(run, rows):
            out[d] = out.get(d, 0) + 1
    return out
