"""Find a school's official website with Google Places API (New) Text Search, then confirm it on the site itself.

Request: POST https://places.googleapis.com/v1/places:searchText, header X-Goog-FieldMask selects fields.
Requesting places.websiteUri bills the Text Search Enterprise SKU (1,000 free calls/month globally, as of Oct 2026 —
see docs/PHASE_1B_RESEARCH_REPORT.md F3). Places ToS lets us keep only place_id: the website URL is NOT stored
from Places. It is stored only after the school's own homepage is fetched and confirmed to name the school, and the
stored source is that homepage.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from .fetch import SafeFetcher
from .signals import visible_text

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = "places.id,places.displayName,places.websiteUri"
STOP = {"limited", "ltd", "t/a", "ta", "the", "and", "of", "school", "academy", "aviation", "flight", "flying",
        "training", "centre", "center", "services", "group", "plc", "llp", "inc", "llc", "gmbh", "s.l.", "sl", "aero"}
COUNTRY = {"GB": "United Kingdom", "US": "United States", "CA": "Canada", "AU": "Australia", "ES": "Spain", "IE": "Ireland"}


class PlacesError(RuntimeError):
    pass


def name_tokens(name: str) -> set[str]:
    parts = re.split(r"\bT/A\b", name, flags=re.I)
    toks = set()
    for part in parts:
        toks |= {t for t in re.findall(r"[a-z0-9]+", part.lower()) if t not in STOP and len(t) > 2}
    return toks


def search(name: str, city: str | None, country: str, api_key: str | None = None, session=None) -> list[dict]:
    import requests
    key = api_key or os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        raise PlacesError("GOOGLE_PLACES_API_KEY is not set")
    trading = re.split(r"\bT/A\b", name, flags=re.I)[-1].strip()
    q = " ".join(x for x in (trading, "flight training", city, COUNTRY.get(country, country)) if x)
    r = (session or requests).post(ENDPOINT, timeout=20, json={"textQuery": q, "pageSize": 5},
                                   headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK})
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:
            msg = r.text[:200]
        raise PlacesError(f"Places API {r.status_code}: {msg[:300]}")
    return r.json().get("places", [])


def domain_of(url: str) -> str | None:
    h = (urlsplit(url).hostname or "").lower()
    return (h[4:] if h.startswith("www.") else h) or None


def confirm_on_site(name: str, approval_ref: str | None, url: str, fetcher: SafeFetcher) -> dict | None:
    """Fetch the candidate homepage; accept it only if it names the school (or shows its approval number)."""
    page = fetcher.get(url)
    hops = 0
    while page.status in (301, 302, 303, 307, 308) and hops < 3 and page.url != url:
        url, page, hops = page.url, fetcher.get(page.url), hops + 1
    if page.status != 200 or not page.html:
        return None
    text = visible_text(page.html).lower()
    toks = name_tokens(name)
    hits = {t for t in toks if re.search(rf"\b{re.escape(t)}\b", text)}
    ref_hit = bool(approval_ref and approval_ref.lower() in text)
    if ref_hit or (toks and len(hits) / len(toks) >= 0.6):
        return {"url": page.url, "domain": domain_of(page.url), "matched_tokens": sorted(hits), "approval_ref_on_site": ref_hit}
    return None


def find_website(name: str, city: str | None, country: str, approval_ref: str | None, fetcher: SafeFetcher,
                 api_key: str | None = None, session=None) -> dict:
    """-> {status: found|not_found, place_id?, url?, domain?, ...}. Raises PlacesError on API problems."""
    toks = name_tokens(name)
    for p in search(name, city, country, api_key, session):
        disp = (p.get("displayName") or {}).get("text", "")
        web = p.get("websiteUri")
        if not web or not toks:
            continue
        overlap = len(toks & name_tokens(disp)) / len(toks)
        if overlap < 0.5:
            continue
        try:
            ok = confirm_on_site(name, approval_ref, web, fetcher)
        except Exception:
            ok = None
        if ok:
            return {"status": "found", "place_id": p.get("id"), **ok}
    return {"status": "not_found"}
