"""World sweep: find every flying school on Earth, country by country, inside Google's free monthly allowance.

The map is cut into 1-degree squares that hold at least one airfield (config/world_cells.json, built from OurAirports
by scripts/build_world_cells.py). Each square gets one Places Text Search "flight school" restricted to the square
(locationRestriction, so nothing outside it is returned), up to 3 pages of 20. A square that fills all 3 pages is
split into four smaller squares, down to about 14 km, so dense areas are not cut short.

Field mask = Text Search Pro fields only (id, name, address, location, address parts, status, types): no website or
phone, which would bill the 5x smaller Enterprise allowance. Websites are found afterwards by the normal
find-websites stage, one school at a time, within its own allowance.

Countries go in the owner's order (main training markets first) and the sweep starts again after RESWEEP_DAYS to
catch new schools. Every school seen is matched to an existing record (Google place id, then name in the same
country) before anything is created, and logged in flightbridge.detection with its position for the live globe.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import db
from . import quota
from .discover import NOT_A_SCHOOL, RELEVANT, _component, match_existing
from .places import ENDPOINT, PlacesError

WORLD_PATH = Path(__file__).resolve().parents[1] / "config" / "world_cells.json"
SKU = "text_search_pro"
MASK = ("places.id,places.displayName,places.formattedAddress,places.location,places.addressComponents,"
        "places.businessStatus,places.types,nextPageToken")
QUERY = "flight school"
PAGES = 3                 # Google returns at most 60 results (3 pages of 20) per search
MAX_DEPTH = 3             # 1 deg -> 0.5 -> 0.25 -> 0.125 deg (~14 km)
RESWEEP_DAYS = 180
MAX_CALLS_PER_CYCLE = 12  # a 15-minute cycle never spends more than this, whatever the catch-up allowance says


def load_world() -> dict:
    return json.loads(WORLD_PATH.read_text(encoding="utf-8"))


def seed(database_url: str, pass_no: int = 1) -> dict:
    """Queue every square for this pass (idempotent). Countries keep their place in the order."""
    world = load_world()
    with db.tx(database_url) as conn:
        for prio, country in enumerate(world["order"], start=1):
            squares = world["cells"][country]
            conn.execute("""INSERT INTO flightbridge.sweep_country (country, priority, pass, cells_total)
                            VALUES (%s,%s,%s,%s)
                            ON CONFLICT (country) DO UPDATE SET priority=EXCLUDED.priority""",
                         (country, prio, pass_no, len(squares)))
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO flightbridge.sweep_cell (country, pass, lat0, lng0, lat1, lng1, depth)
                                   VALUES (%s,%s,%s,%s,%s,%s,0) ON CONFLICT DO NOTHING""",
                                [(country, pass_no, la, lo, la + 1, lo + 1) for la, lo in squares])
        n = conn.execute("SELECT count(*) FROM flightbridge.sweep_cell WHERE pass=%s", (pass_no,)).fetchone()[0]
    return {"pass": pass_no, "countries": len(world["order"]), "squares": n}


def _ensure_seeded(database_url: str) -> None:
    with db.tx(database_url) as conn:
        has = conn.execute("SELECT 1 FROM flightbridge.sweep_cell LIMIT 1").fetchone()
    if not has:
        seed(database_url, 1)


def _maybe_resweep(database_url: str) -> dict | None:
    """When every square of the current pass is done and the pass is old enough, queue the next pass."""
    with db.tx(database_url) as conn:
        cur = conn.execute("SELECT max(pass) FROM flightbridge.sweep_cell").fetchone()[0]
        if cur is None:
            return None
        open_ = conn.execute("SELECT 1 FROM flightbridge.sweep_cell WHERE pass=%s AND status IN ('queued','scanning') LIMIT 1",
                             (cur,)).fetchone()
        if open_:
            return None
        due = conn.execute("""SELECT min(searched_at) < now() - make_interval(days => %s) FROM flightbridge.sweep_cell
                              WHERE pass=%s""", (RESWEEP_DAYS, cur)).fetchone()[0]
        if not due:
            return None
        conn.execute("""UPDATE flightbridge.sweep_country SET pass=%s, cells_done=0, schools_found=0, schools_new=0,
                        started_at=NULL, finished_at=NULL""", (cur + 1,))
    return seed(database_url, cur + 1)


def _next_cells(database_url: str, limit: int) -> list[dict]:
    with db.tx(database_url) as conn:
        # a square left 'scanning' by a runner that died is picked up again after an hour
        conn.execute("""UPDATE flightbridge.sweep_cell SET status='queued' WHERE status='scanning'
                        AND searched_at < now() - interval '1 hour'""")
        rows = conn.execute("""
            SELECT c.id, c.country, c.pass, c.lat0, c.lng0, c.lat1, c.lng1, c.depth
              FROM flightbridge.sweep_cell c
              JOIN flightbridge.sweep_country k ON k.country = c.country AND k.pass = c.pass AND k.enabled
             WHERE c.status = 'queued'
             ORDER BY k.priority, c.depth DESC, c.id
             LIMIT %s""", (limit,)).fetchall()
    keys = ("id", "country", "pass", "lat0", "lng0", "lat1", "lng1", "depth")
    return [dict(zip(keys, r)) for r in rows]


def search_page(cell: dict, page_token: str | None = None, api_key: str | None = None, session=None) -> dict:
    import requests
    from .. import secrets
    key = api_key or secrets.get("google_places_api_key")
    if not key:
        raise PlacesError("GOOGLE_PLACES_API_KEY is not set")
    body = {"textQuery": QUERY, "pageSize": 20,
            "locationRestriction": {"rectangle": {"low": {"latitude": cell["lat0"], "longitude": cell["lng0"]},
                                                  "high": {"latitude": cell["lat1"], "longitude": cell["lng1"]}}}}
    if page_token:
        body["pageToken"] = page_token
    r = (session or requests).post(ENDPOINT, json=body, timeout=25,
                                   headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": MASK})
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:
            msg = getattr(r, "text", "")[:200]
        raise PlacesError(f"Places API {r.status_code}: {msg[:300]}")
    return r.json()


def record_place(database_url: str, p: dict, cell: dict) -> tuple | None:
    """Match or create the school for one Google result; log the detection. -> (org_id, is_new) or None (skipped)."""
    pid = p.get("id")
    name = ((p.get("displayName") or {}).get("text") or "").strip()
    loc = p.get("location") or {}
    if not pid or not name or "latitude" not in loc:
        return None
    types = " ".join(p.get("types") or [])
    country = (_component(p, "country", short=True) or cell["country"]).upper()[:2]
    with db.tx(database_url) as conn:
        oid = match_existing(conn, pid, None, name, country)
        if oid is None and (not (RELEVANT["training_hub"].search(name) or RELEVANT["training_hub"].search(types))
                            or NOT_A_SCHOOL.search(name)):
            return None
        is_new = oid is None
        if is_new:
            region = _component(p, "administrative_area_level_1")
            city = _component(p, "locality") or _component(p, "postal_town") or _component(p, "administrative_area_level_2")
            oid = conn.execute(
                """INSERT INTO flightbridge.organisation (name, country, region, city, org_kind, status)
                   VALUES (%s,%s,%s,%s,'flying_school','research_pending') RETURNING id""",
                (name, country, region, city)).fetchone()[0]
            db.audit(conn, "organisation_created", "organisation", oid,
                     {"source": "google_places", "sweep_cell": cell["id"], "query": QUERY})
        conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                        VALUES (%s,'google_place_id',%s,NULL) ON CONFLICT (scheme, value) DO NOTHING""", (oid, pid))
        if p.get("businessStatus") == "CLOSED_PERMANENTLY" and not conn.execute(
                """SELECT 1 FROM flightbridge.audit_log WHERE entity='organisation' AND entity_id=%s AND action='place_closed'""",
                (str(oid),)).fetchone():
            db.audit(conn, "place_closed", "organisation", oid, {"place_id": pid})
        conn.execute("""INSERT INTO flightbridge.detection (org_id, cell_id, country, name, lat, lng, is_new)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                     (oid, cell["id"], country, name, loc["latitude"], loc["longitude"], is_new))
    return oid, is_new


def _account_level(exc: Exception) -> bool:
    """Google errors about the key, billing or quota rather than about the square being searched."""
    m = re.search(r"Places API (\d{3})", str(exc))
    return bool(m) and (m.group(1) in ("401", "403", "429") or m.group(1).startswith("5"))


def requeue_refused(database_url: str) -> int:
    """Squares marked 'error' only because Google refused the call are put back in the queue."""
    with db.tx(database_url) as conn:
        n = conn.execute("""UPDATE flightbridge.sweep_cell SET status='queued'
                            WHERE status='error' AND error ~ '^Places API (401|403|429|5[0-9][0-9])'""").rowcount
        if n:
            conn.execute("""UPDATE flightbridge.sweep_country k SET finished_at=NULL,
                              cells_done = (SELECT count(*) FROM flightbridge.sweep_cell c WHERE c.country=k.country
                                             AND c.pass=k.pass AND c.status IN ('done','split','error'))""")
    return n


def scan_cell(database_url: str, cell: dict, session=None) -> dict:
    """Search one square (up to 3 pages). Returns {results, schools, new, calls, split}."""
    with db.tx(database_url) as conn:
        conn.execute("UPDATE flightbridge.sweep_cell SET status='scanning', searched_at=now() WHERE id=%s", (cell["id"],))
        conn.execute("""UPDATE flightbridge.sweep_country SET started_at=coalesce(started_at, now())
                        WHERE country=%s AND pass=%s""", (cell["country"], cell["pass"]))
    out = {"results": 0, "schools": 0, "new": 0, "calls": 0, "split": False}
    token, seen = None, set()
    try:
        for _ in range(PAGES):
            data = search_page(cell, token, session=session)
            out["calls"] += 1
            for p in data.get("places") or []:
                if p.get("id") in seen:
                    continue
                seen.add(p.get("id"))
                out["results"] += 1
                got = record_place(database_url, p, cell)
                if got:
                    out["schools"] += 1
                    out["new"] += got[1]
            token = data.get("nextPageToken")
            if not token:
                break
    except PlacesError as exc:
        with db.tx(database_url) as conn:
            # our own free-limit guard, or Google refusing the key/project (403 permission, 429 daily quota, 401, 5xx):
            # the square was never searched, so it goes back in the queue and is tried again on a later cycle.
            # Only an error about this particular square (e.g. 400 bad request) marks it as failed.
            if isinstance(exc, quota.QuotaExceeded) or _account_level(exc):
                conn.execute("UPDATE flightbridge.sweep_cell SET status='queued', error=%s WHERE id=%s",
                             (str(exc)[:500], cell["id"]))
            else:
                conn.execute("UPDATE flightbridge.sweep_cell SET status='error', error=%s, calls=calls+%s WHERE id=%s",
                             (str(exc)[:500], out["calls"], cell["id"]))
        raise
    full = token is not None or out["results"] >= PAGES * 20
    out["split"] = full and cell["depth"] < MAX_DEPTH
    with db.tx(database_url) as conn:
        if out["split"]:
            mid_lat, mid_lng = (cell["lat0"] + cell["lat1"]) / 2, (cell["lng0"] + cell["lng1"]) / 2
            kids = [(cell["lat0"], cell["lng0"], mid_lat, mid_lng), (cell["lat0"], mid_lng, mid_lat, cell["lng1"]),
                    (mid_lat, cell["lng0"], cell["lat1"], mid_lng), (mid_lat, mid_lng, cell["lat1"], cell["lng1"])]
            for la0, lo0, la1, lo1 in kids:
                conn.execute("""INSERT INTO flightbridge.sweep_cell (country, pass, lat0, lng0, lat1, lng1, depth, parent_id)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                             (cell["country"], cell["pass"], la0, lo0, la1, lo1, cell["depth"] + 1, cell["id"]))
        conn.execute("""UPDATE flightbridge.sweep_cell SET status=%s, results=%s, schools=%s, new_schools=%s, calls=calls+%s,
                        searched_at=now(), error=NULL WHERE id=%s""",
                     ("split" if out["split"] else "done", out["results"], out["schools"], out["new"], out["calls"], cell["id"]))
        conn.execute("""UPDATE flightbridge.sweep_country k SET
                          cells_total = (SELECT count(*) FROM flightbridge.sweep_cell c WHERE c.country=k.country AND c.pass=k.pass),
                          cells_done  = (SELECT count(*) FROM flightbridge.sweep_cell c WHERE c.country=k.country AND c.pass=k.pass
                                          AND c.status IN ('done','split','error')),
                          schools_found = k.schools_found + %s, schools_new = k.schools_new + %s
                        WHERE country=%s AND pass=%s""", (out["schools"], out["new"], cell["country"], cell["pass"]))
        conn.execute("""UPDATE flightbridge.sweep_country k SET finished_at=now()
                        WHERE country=%s AND pass=%s AND finished_at IS NULL AND NOT EXISTS (
                          SELECT 1 FROM flightbridge.sweep_cell c WHERE c.country=k.country AND c.pass=k.pass
                            AND c.status IN ('queued','scanning'))""", (cell["country"], cell["pass"]))
    return out


def run(database_url: str, max_calls: int | None = None, session=None) -> dict:
    """One sweep step: scan squares while the paced allowance lasts (a square needs room for 3 pages)."""
    _ensure_seeded(database_url)
    requeued = requeue_refused(database_url)
    resweep = _maybe_resweep(database_url)
    budget = quota.allowance(database_url, SKU) if max_calls is None else max_calls
    budget = min(budget, MAX_CALLS_PER_CYCLE) if max_calls is None else budget
    out = {"squares": 0, "calls": 0, "results": 0, "schools": 0, "new": 0, "split": 0, "allowance": budget}
    if resweep:
        out["resweep"] = resweep
    if requeued:
        out["requeued"] = requeued
    while budget - out["calls"] >= PAGES:
        cells = _next_cells(database_url, 1)
        if not cells:
            out["idle"] = "every country searched; next pass starts after %d days" % RESWEEP_DAYS
            break
        r = scan_cell(database_url, cells[0], session=session)
        out["squares"] += 1
        out["split"] += r["split"]
        for k in ("calls", "results", "schools", "new"):
            out[k] += r[k]
        out["last"] = cells[0]["country"]
    return out


def status(database_url: str) -> dict:
    with db.tx(database_url) as conn:
        rows = conn.execute("""SELECT country, priority, cells_total, cells_done, schools_found, schools_new, started_at, finished_at
                               FROM flightbridge.sweep_country ORDER BY priority""").fetchall()
    keys = ("country", "priority", "cells_total", "cells_done", "schools_found", "schools_new", "started_at", "finished_at")
    return {"countries": [dict(zip(keys, r)) for r in rows], "quota": quota.summary(database_url)}


LOCATE_SKU = "place_details_pro"


def locate_known(database_url: str, limit: int, session=None) -> dict:
    """Put schools found before the sweep (Google place id, no position yet) on the globe: Place Details with the
    location field only (Place Details Pro, its own free allowance). Schools without a Google id are left off."""
    import requests
    from .. import secrets
    key = secrets.get("google_places_api_key")
    if not key or limit <= 0:
        return {"located": 0}
    with db.tx(database_url) as conn:
        rows = conn.execute("""
            SELECT o.id, o.name, o.country, i.value FROM flightbridge.organisation o
              JOIN flightbridge.organisation_identifier i ON i.org_id = o.id AND i.scheme = 'google_place_id'
             WHERE o.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM flightbridge.detection d WHERE d.org_id = o.id)
               AND NOT EXISTS (SELECT 1 FROM flightbridge.audit_log a WHERE a.entity = 'organisation' AND a.entity_id = o.id::text
                               AND a.action = 'google_locate' AND a.at > now() - interval '30 days')
             ORDER BY o.discovered_at LIMIT %s""", (limit,)).fetchall()
    out = {"located": 0, "missing": 0}
    for oid, name, country, pid in rows:
        r = (session or requests).get(f"https://places.googleapis.com/v1/places/{pid}", timeout=20,
                                      headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "location"})
        if r.status_code != 200:
            raise PlacesError(f"Places API {r.status_code}: {getattr(r, 'text', '')[:200]}")
        loc = r.json().get("location") or {}
        with db.tx(database_url) as conn:
            if "latitude" in loc and "longitude" in loc:
                conn.execute("""INSERT INTO flightbridge.detection (org_id, country, name, lat, lng, is_new)
                                VALUES (%s,%s,%s,%s,%s,false)""", (oid, country, name, loc["latitude"], loc["longitude"]))
                out["located"] += 1
            else:
                out["missing"] += 1
            db.audit(conn, "google_locate", "organisation", oid, {"found": "latitude" in loc})
    return out
