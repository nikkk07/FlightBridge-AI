"""Find schools inside an area (Country > State > Area) with Google Places Text Search + a location bias circle.

training_hub areas -> flying schools; demand_hub areas -> aviation institutes / ground schools (kept separate).
Each result is matched to an existing school (Google place id, then website domain, then normalised name in the same
country) before anything new is created, so one school never appears twice.
Google name/address/phone are stored when FlightBridge Settings > "Store Google contact details" is on (owner's
decision, 2026-11-03; note Google Maps Platform terms restrict storing Places content other than place IDs).
"""
from __future__ import annotations

import json
import re

from .. import db, secrets
from .places import PlacesError, domain_of

DISCOVERY_MASK = ("places.id,places.displayName,places.websiteUri,places.formattedAddress,places.location,"
                  "places.addressComponents,places.nationalPhoneNumber,places.internationalPhoneNumber,"
                  "places.businessStatus,places.types")
QUERIES = {
    "training_hub": ["flight school", "flight training academy pilot"],
    "demand_hub": ["aviation academy pilot training institute", "air hostess cabin crew training institute"],
}
RELEVANT = {
    "training_hub": re.compile(r"flight|flying|aviation|aero|pilot|airline|air\s*(?:academy|college|school|training)|heli", re.I),
    "demand_hub": re.compile(r"aviation|pilot|air\s*hostess|cabin\s*crew|aero|flight|airline|airport\s*management", re.I),
}
NOT_A_SCHOOL = re.compile(r"\b(museum|travel|tours?|charter(?!.*(school|academy|training))|parts|repairs?|maintenance(?!.*(school|academy|training))|"
                          r"cafe|café|restaurant|hotel|motel|car rental|rentals?|drone shop|simulator ride|skydiv\w*|balloon)\b", re.I)
LEGAL = re.compile(r"\b(inc|llc|ltd|limited|pty|plc|corp(oration)?|co|company|gmbh|s\.?l\.?|t/a|the)\b\.?", re.I)


def norm_name(name: str) -> str:
    s = LEGAL.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def places_search_area(area: dict, query: str, api_key: str | None = None, session=None) -> list[dict]:
    import requests
    key = api_key or secrets.get("google_places_api_key")
    if not key:
        raise PlacesError("GOOGLE_PLACES_API_KEY is not set")
    body = {"textQuery": query, "pageSize": 20,
            "locationBias": {"circle": {"center": {"latitude": area["lat"], "longitude": area["lng"]},
                                        "radius": float(min(area["radius_km"], 50) * 1000)}}}
    from .places import ENDPOINT
    r = (session or requests).post(ENDPOINT, json=body, timeout=25,
                                   headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": DISCOVERY_MASK})
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:
            msg = getattr(r, "text", "")[:200]
        raise PlacesError(f"Places API {r.status_code}: {msg[:300]}")
    return r.json().get("places", [])


def _component(place: dict, kind: str, short: bool = False) -> str | None:
    for c in place.get("addressComponents") or []:
        if kind in (c.get("types") or []):
            return c.get("shortText" if short else "longText")
    return None


def _distance_km(a_lat, a_lng, b_lat, b_lng) -> float:
    from math import asin, cos, radians, sin, sqrt
    dlat, dlng = radians(b_lat - a_lat), radians(b_lng - a_lng)
    h = sin(dlat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(dlng / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def match_existing(conn, place_id: str, domain: str | None, name: str, country: str):
    hit = conn.execute("SELECT org_id FROM flightbridge.organisation_identifier WHERE scheme='google_place_id' AND value=%s",
                       (place_id,)).fetchone()
    if hit:
        return hit[0]
    if domain:
        hit = conn.execute("SELECT id FROM flightbridge.organisation WHERE lower(website_domain)=%s AND deleted_at IS NULL",
                           (domain,)).fetchone()
        if hit:
            return hit[0]
    n = norm_name(name)
    if len(n) >= 6:
        for oid, other in conn.execute("SELECT id, name FROM flightbridge.organisation WHERE country=%s AND deleted_at IS NULL",
                                       (country,)).fetchall():
            on = norm_name(other)
            if on == n or (len(on) >= 8 and (on in n or n in on)):
                return oid
    return None


def search_area(database_url: str, area_id: int, session=None) -> dict:
    from .pipeline import store_phone
    with db.tx(database_url) as conn:
        row = conn.execute("""SELECT id, country, region, name, kind, lat, lng, radius_km FROM flightbridge.area
                              WHERE id=%s""", (area_id,)).fetchone()
    if not row:
        return {"error": "area_not_found"}
    area = dict(zip(("id", "country", "region", "name", "kind", "lat", "lng", "radius_km"), row))
    store_google = bool(secrets.settings(database_url).get("store_google_contacts", True))
    seen, out = set(), {"area": f"{area['name']}, {area['region']}", "results": 0, "new": 0, "matched": 0, "skipped": 0,
                        "closed": 0, "phones": 0}
    rel = RELEVANT[area["kind"]]
    for q in QUERIES[area["kind"]]:
        for p in places_search_area(area, q, session=session):
            pid = p.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            out["results"] += 1
            name = ((p.get("displayName") or {}).get("text") or "").strip()
            types = " ".join(p.get("types") or [])
            web = p.get("websiteUri")
            domain = domain_of(web) if web else None
            country = (_component(p, "country", short=True) or area["country"]).upper()[:2]
            with db.tx(database_url) as conn:
                known = match_existing(conn, pid, domain, name, country) if name else None
            # the name filter only gates NEW records; a school we already know (register, website) is always matched
            if not name or (known is None and (not (rel.search(name) or rel.search(types)) or NOT_A_SCHOOL.search(name))):
                out["skipped"] += 1
                continue
            loc = p.get("location") or {}
            if loc and _distance_km(area["lat"], area["lng"], loc.get("latitude", area["lat"]),
                                    loc.get("longitude", area["lng"])) > area["radius_km"] * 1.5:
                out["skipped"] += 1           # location bias is a preference, not a fence
                continue
            region = _component(p, "administrative_area_level_1") or area["region"]
            city = _component(p, "locality") or _component(p, "postal_town") or area["name"]
            kind = "flying_school" if area["kind"] == "training_hub" else "ground_school"
            with db.tx(database_url) as conn:
                oid = match_existing(conn, pid, domain, name, country)
                if oid is None:
                    if domain and conn.execute("SELECT 1 FROM flightbridge.organisation WHERE lower(website_domain)=%s",
                                               (domain,)).fetchone():
                        domain = None         # domain belongs to another (deleted) record; keep it unique
                    oid = conn.execute(
                        """INSERT INTO flightbridge.organisation (name, country, region, city, website_domain, org_kind, status, area_id)
                           VALUES (%s,%s,%s,%s,%s,%s,'research_pending',%s) RETURNING id""",
                        (name, country, region, city, domain, kind, area["id"])).fetchone()[0]
                    db.audit(conn, "organisation_created", "organisation", oid,
                             {"source": "google_places", "area": out["area"], "query": q})
                    out["new"] += 1
                else:
                    free = domain if domain and not conn.execute(
                        "SELECT 1 FROM flightbridge.organisation WHERE lower(website_domain)=%s AND id<>%s", (domain, oid)).fetchone() else None
                    conn.execute("""UPDATE flightbridge.organisation SET area_id=coalesce(area_id,%s),
                                    website_domain=coalesce(website_domain,%s), updated_at=now() WHERE id=%s""",
                                 (area["id"], free, oid))
                    out["matched"] += 1
                conn.execute("""INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value, source_url)
                                VALUES (%s,'google_place_id',%s,NULL) ON CONFLICT (scheme, value) DO NOTHING""", (oid, pid))
                if p.get("businessStatus") == "CLOSED_PERMANENTLY":
                    out["closed"] += 1
                    db.audit(conn, "place_closed", "organisation", oid, {"place_id": pid})
                if store_google:
                    phone = p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber")
                    if phone:
                        out["phones"] += store_phone(conn, oid, phone, "google_places",
                                                     f"https://www.google.com/maps/place/?q=place_id:{pid}")
    with db.tx(database_url) as conn:
        conn.execute("UPDATE flightbridge.area SET last_searched_at=now(), last_result=%s WHERE id=%s",
                     (json.dumps(out), area["id"]))
        db.audit(conn, "area_searched", "area", area["id"], out)
    return out


def due_areas(database_url: str, limit: int) -> list[int]:
    """Enabled areas never searched, or searched longest ago (re-search every 30 days to catch new schools)."""
    with db.tx(database_url) as conn:
        return [r[0] for r in conn.execute("""
            SELECT id FROM flightbridge.area WHERE enabled
               AND (last_searched_at IS NULL OR last_searched_at < now() - interval '30 days')
             ORDER BY last_searched_at NULLS FIRST, id LIMIT %s""", (limit,)).fetchall()]


def assign_areas(database_url: str) -> int:
    """Put register-imported schools (no area yet) into an area of their region when the city names match."""
    with db.tx(database_url) as conn:
        rows = conn.execute("""SELECT o.id, a.id FROM flightbridge.organisation o
                               JOIN flightbridge.area a ON a.country=o.country AND a.region=o.region
                              WHERE o.area_id IS NULL AND o.city IS NOT NULL AND o.deleted_at IS NULL
                                AND position(lower(o.city) in lower(a.name)) > 0""").fetchall()
        for oid, aid in rows:
            conn.execute("UPDATE flightbridge.organisation SET area_id=%s WHERE id=%s AND area_id IS NULL", (aid, oid))
    return len(rows)


def geocode(name: str, region: str, country: str, session=None) -> tuple[float, float] | None:
    """Coordinates for a new area typed in the app (Places Text Search, location field only)."""
    from .places import search
    res = search("", None, country, session=session, field_mask="places.location", query=f"{name}, {region}")
    for p in res:
        loc = p.get("location")
        if loc:
            return loc["latitude"], loc["longitude"]
    return None
