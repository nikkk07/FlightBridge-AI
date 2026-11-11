"""Keep every Google Maps Platform call inside the free monthly allowance.

Google's free monthly caps per SKU (Places API (New), checked 2026-11-04 on
https://developers.google.com/maps/billing-and-pricing/pricing): Text Search Essentials (IDs Only) and Place Details
Essentials (IDs Only) unlimited; Text Search Pro and Place Details Pro 5,000; Text Search Enterprise and Place Details
Enterprise 1,000. The caps here leave a 10% margin for calls made elsewhere (the CRM's "add area" geocode).

The SKU is decided by the most expensive field asked for in X-Goog-FieldMask. Every call is counted in
flightbridge.google_usage BEFORE it is made, in one atomic statement shared by every runner; once a SKU reaches its
cap the call is refused with QuotaExceeded (a PlacesError, so the stage stops and reports it) until the next month.

install(database_url) wraps requests.Session.request for places.googleapis.com, so engine code needs no changes.

When Google refuses a call it is not billed, so the count is given back. Google refusing the key also pauses every
Google call (no pointless retries) and resumes by itself: a daily-quota 429 until Google's daily reset (midnight
Pacific time), a 403/401 for one hour, then a single test call decides. The pause is stored in
system_setting 'google_pause' so every runner (CRM server, Mac) shares it and the CRM can show it.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from .. import db
from .places import PlacesError

HOST = "places.googleapis.com"
DEFAULT_CAPS = {
    "text_search_pro": 4500,
    "text_search_enterprise": 900,
    "place_details_pro": 4500,
    "place_details_enterprise": 900,
    "tavily_search": 900,          # Tavily web search: 1,000 free credits a month (basic search = 1 credit)
}
# Text Search: Pro fields (anything beyond the place id); Enterprise adds website, phone, rating, opening hours ...
ENTERPRISE_FIELDS = ("websiteuri", "phonenumber", "rating", "usersratingcount", "openinghours", "pricelevel", "pricerange")
ID_ONLY = {"places.id", "id", "places.name", "name", "nextpagetoken", "places.attributions", "attributions"}


class QuotaExceeded(PlacesError):
    pass


def classify(method: str, url: str, field_mask: str | None) -> str | None:
    """-> SKU name, or None for calls Google does not charge for (IDs only)."""
    path = urlsplit(url).path
    fields = {f.strip().lower() for f in (field_mask or "").split(",") if f.strip()}
    kind = "text_search" if path.endswith(":searchText") else "place_details" if "/places/" in path else None
    if kind is None:
        return "text_search_enterprise"          # unknown endpoint: count it against the smallest allowance
    if fields and fields <= ID_ONLY:
        return None
    if not fields or "*" in fields or any(any(e in f for e in ENTERPRISE_FIELDS) for f in fields):
        return f"{kind}_enterprise"
    return f"{kind}_pro"


def month_start(now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    return now.date().replace(day=1)


def caps(database_url: str) -> dict:
    out = dict(DEFAULT_CAPS)
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='google_budget'").fetchone()
    if row and isinstance(row[0], dict):
        for k, v in row[0].items():
            if k in out and isinstance(v, int) and 0 <= v <= DEFAULT_CAPS[k] / 0.9:   # never above Google's free cap
                out[k] = v
    return out


def reserve(database_url: str, sku: str, n: int = 1) -> int:
    """Count n calls of sku this month; raise QuotaExceeded if that would pass the cap. Returns the new total."""
    cap = caps(database_url)[sku]
    with db.tx(database_url) as conn:
        row = conn.execute("""
            INSERT INTO flightbridge.google_usage AS u (month, sku, calls, updated_at) VALUES (%s, %s, %s, now())
            ON CONFLICT (month, sku) DO UPDATE SET calls = u.calls + EXCLUDED.calls, updated_at = now()
                WHERE u.calls + EXCLUDED.calls <= %s
            RETURNING calls""", (month_start(), sku, n, cap)).fetchone()
        if row is None or row[0] > cap:           # raising rolls the count back
            raise QuotaExceeded(f"Google free monthly limit reached for {sku.replace('_', ' ')} ({cap} calls); "
                                f"resumes on the 1st of next month")
    return row[0]


def used(database_url: str) -> dict:
    with db.tx(database_url) as conn:
        rows = conn.execute("SELECT sku, calls FROM flightbridge.google_usage WHERE month=%s", (month_start(),)).fetchall()
    return {sku: calls for sku, calls in rows}


def release(database_url: str, sku: str, n: int = 1) -> None:
    """Give back n counted calls (Google refused them, so they were not billed)."""
    with db.tx(database_url) as conn:
        conn.execute("""UPDATE flightbridge.google_usage SET calls = greatest(0, calls - %s), updated_at = now()
                        WHERE month=%s AND sku=%s""", (n, month_start(), sku))


PAUSE_KEY = "google_pause"


def next_daily_reset(now: datetime | None = None) -> datetime:
    """Google's per-day quotas reset at midnight Pacific time."""
    pt = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/Los_Angeles"))
    return (pt + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0).astimezone(timezone.utc)


def pause_state(database_url: str) -> dict | None:
    with db.tx(database_url) as conn:
        row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key=%s", (PAUSE_KEY,)).fetchone()
    return row[0] if row and row[0] else None


def set_pause(database_url: str, code: int, message: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    until = next_daily_reset(now) if code == 429 else now + timedelta(hours=1)
    state = {"code": code, "message": message[:300], "until": until.isoformat(), "since": now.isoformat()}
    with db.tx(database_url) as conn:
        old = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key=%s", (PAUSE_KEY,)).fetchone()
        if old and old[0] and old[0].get("since"):
            state["since"] = old[0]["since"]          # keep when the trouble started
        conn.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (PAUSE_KEY, json.dumps(state)))
    return state


def clear_pause(database_url: str) -> None:
    with db.tx(database_url) as conn:
        conn.execute("DELETE FROM flightbridge.system_setting WHERE key=%s", (PAUSE_KEY,))


def check_pause(database_url: str, now: datetime | None = None) -> None:
    """Raise without calling Google while a pause is running."""
    st = pause_state(database_url)
    if st and datetime.fromisoformat(st["until"]) > (now or datetime.now(timezone.utc)):
        raise PlacesError(f"Places API {st['code']}: {st['message']} (paused, retrying after {st['until'][:16]} UTC)")


def allowance(database_url: str, sku: str, burst: int = 6, now: datetime | None = None) -> int:
    """Calls this SKU may make right now so the month's allowance is spread evenly (plus a small catch-up burst)."""
    now = now or datetime.now(timezone.utc)
    start = datetime.combine(month_start(now), datetime.min.time(), timezone.utc)
    nxt = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    frac = (now - start).total_seconds() / (nxt - start).total_seconds()
    cap = caps(database_url)[sku]
    spent = used(database_url).get(sku, 0)
    return max(0, min(cap - spent, int(cap * frac) + burst - spent))


_installed: dict = {}
_lock = threading.Lock()


def install(database_url: str) -> None:
    """Meter every request to places.googleapis.com made through `requests` in this process."""
    import requests
    with _lock:
        _installed["url"] = database_url
        if _installed.get("patched"):
            return
        original = requests.Session.request

        def metered(self, method, url, *args, **kwargs):
            if (urlsplit(str(url)).hostname or "").lower() == HOST and _installed.get("url"):
                headers = kwargs.get("headers") or {}
                mask = next((v for k, v in headers.items() if k.lower() == "x-goog-fieldmask"), None)
                sku = classify(method, str(url), mask)
                dbu = _installed["url"]
                check_pause(dbu)
                if sku:
                    reserve(dbu, sku)
                resp = original(self, method, url, *args, **kwargs)
                code = getattr(resp, "status_code", 200)
                if code >= 400 and sku:
                    release(dbu, sku)                  # refused calls are not billed
                if code in (401, 403, 429):
                    try:
                        msg = resp.json().get("error", {}).get("message", "")
                    except Exception:
                        msg = ""
                    set_pause(dbu, code, msg or f"HTTP {code}")
                elif code < 400 and pause_state(dbu):
                    clear_pause(dbu)                   # Google answers again: back to normal
                return resp
            return original(self, method, url, *args, **kwargs)

        requests.Session.request = metered
        _installed["patched"] = True


def summary(database_url: str) -> dict:
    c, u = caps(database_url), used(database_url)
    return {"month": month_start().isoformat(), "skus": {k: {"used": u.get(k, 0), "cap": v} for k, v in c.items()}}

