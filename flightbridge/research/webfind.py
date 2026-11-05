"""Find a school's own website without spending Google's free allowance.

Two methods, used before the Google Places website look-up (which is capped at ~900 a month):

1. guess()  — free and unlimited. Builds the school's likely web addresses from its name (e.g. "Sheble Aviation" in the
              US -> shebleaviation.com, sheble-aviation.com, sheble.aero ...), keeps the ones that exist (DNS), and
              opens the homepage.
2. tavily() — Tavily web search (https://docs.tavily.com, 1,000 free credits a month, 1 credit per basic search), paced
              inside 90 % of that through research/quota.py (sku "tavily_search").

Either way a page is accepted ONLY when the engine itself has read it and it
  * names the school (>= 60 % of the distinctive name words, or shows its approval number),
  * is about flying (pilot / flight / aviation / aircraft ... words), and
  * is not a parked or for-sale domain.
  * and is tied to this school: its town on the page, or 2+ name words plus its state, or 3+ name words
    (or its approval number).
So nothing is taken on trust from a search snippet or a guess.
"""
from __future__ import annotations

import re
import socket
from urllib.parse import urlsplit

from .fetch import SafeFetcher
from .places import domain_of, name_tokens
from .signals import visible_text

TAVILY = "https://api.tavily.com/search"
SKU = "tavily_search"

LEGAL = {"llc", "inc", "incorporated", "ltd", "limited", "pty", "pvt", "private", "co", "corp", "corporation", "company",
         "the", "and", "of", "plc", "gmbh", "sa", "srl", "lp", "llp", "dba"}
GENERIC = {"aviation", "flight", "flying", "school", "academy", "training", "center", "centre", "club", "aero", "air",
           "services", "service", "pilot", "pilots", "college", "institute", "international", "helicopters", "helicopter"}
TLDS = {
    "US": (".com", ".aero", ".org", ".net", ".us"), "CA": (".ca", ".com", ".aero"), "AU": (".com.au", ".au", ".com", ".aero"),
    "NZ": (".co.nz", ".nz", ".com", ".aero"), "GB": (".co.uk", ".uk", ".com", ".aero"), "IE": (".ie", ".com", ".aero"),
    "IN": (".in", ".co.in", ".com", ".aero"), "ZA": (".co.za", ".com", ".aero"),
}
FLYING = re.compile(r"\b(pilot|pilots|flight|flying|aviation|aircraft|aeroplane|airplane|cessna|piper|cfi|instructor|"
                    r"ppl|cpl|atpl|private pilot|commercial pilot|helicopter|airport|hangar)\b", re.I)
PARKED = re.compile(r"(domain (?:name )?(?:is |may be )?for sale|buy this domain|this domain is parked|parked free|"
                    r"hugedomains|sedo\.com|dan\.com|afternic|godaddy\.com/domainsearch|domain has expired|"
                    r"under construction|coming soon)", re.I)
# Directories, social networks and map sites: never a school's own website
NOT_OWN_SITE = re.compile(r"(^|\.)(facebook|instagram|linkedin|twitter|x|youtube|tiktok|yelp|yellowpages|mapquest|"
                          r"tripadvisor|google|bing|apple|wikipedia|wikimapia|foursquare|bbb|manta|zoominfo|dnb|"
                          r"opencorporates|bizapedia|indeed|glassdoor|airnav|aopa|flightschoollist|flighttrainingcentral|"
                          r"pilotcareernews|faa|gov|reddit|quora|crunchbase|chamberofcommerce|hotfrog|cylex|"
                          r"nicelocal|birdeye|angi|thumbtack|groupon|eventbrite|medium|wordpress\.com|blogspot)\.",
                          re.I)


def labels(name: str) -> list[str]:
    """Likely domain labels for a school name, best first (at most 5)."""
    trading = re.split(r"\b(?:T/A|DBA|d/b/a)\b", name or "", flags=re.I)[-1]
    words = [w for w in re.findall(r"[a-z0-9]+", trading.lower().replace("&", " and ")) if w not in LEGAL]
    if not words:
        return []
    core = [w for w in words if w not in GENERIC]
    out = []
    for cand in ("".join(words), "-".join(words), "".join(words[:2]) if len(words) > 2 else "",
                 "".join(core) if core and core != words else "",
                 "".join(core) + "aviation" if core and "aviation" not in words else "",
                 "fly" + "".join(core) if core and len(core) == 1 else ""):
        if cand and 4 <= len(cand) <= 40 and cand not in out and not cand.startswith("-"):
            out.append(cand)
    return out[:5]


def candidates(name: str, country: str) -> list[str]:
    tlds = TLDS.get((country or "").upper(), (".com", f".{(country or 'com').lower()}", ".aero"))
    return [lab + t for lab in labels(name) for t in tlds]


def resolves(domain: str, timeout: float = 3.0) -> bool:
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        socket.getaddrinfo(domain, 443)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(old)


def confirm_site(name: str, url: str, fetcher: SafeFetcher, approval_ref: str | None = None,
                 places: tuple = ()) -> dict | None:
    """Read the page; accept it only if it names the school, is about flying and is not parked."""
    try:
        page = fetcher.get(url)
        hops = 0
        while page.status in (301, 302, 303, 307, 308) and hops < 3 and page.url != url:
            url, page, hops = page.url, fetcher.get(page.url), hops + 1
    except Exception:
        return None
    if page.status != 200 or not page.html:
        return None
    dom = domain_of(page.url)
    if not dom or NOT_OWN_SITE.search(dom + "."):
        return None
    text = visible_text(page.html)
    low = text.lower()
    if PARKED.search(low[:4000]) or not FLYING.search(low):
        return None
    toks = name_tokens(name)
    hits = {t for t in toks if re.search(rf"\b{re.escape(t)}\b", low)}
    ref_hit = bool(approval_ref and approval_ref.lower() in low)
    place_hit = [p for p in places if p and len(p) > 2 and re.search(rf"\b{re.escape(p.lower())}\b", low)]
    city_hit = bool(places and places[0] and places[0] in place_hit)
    # One matching word ("Rotor", "Tailwinds", "Vertical") is too weak on its own: many other businesses share it.
    # Accept: the approval number on the page; or the name plus the school's town; or 2+ name words plus its state;
    # or 3+ name words.
    if not toks:            # name is only generic words ("Flight Training Center"): require the town and every word
        ok = ref_hit or city_hit and all(w in low for w in re.findall(r"[a-z]{4,}", name.lower())[:4])
    else:
        ok = ref_hit or (len(hits) / len(toks) >= 0.6 and (city_hit or (len(hits) >= 2 and bool(place_hit)) or len(hits) >= 3))
    if not ok:
        return None
    return {"url": page.url, "domain": dom, "matched_tokens": sorted(hits), "approval_ref_on_site": ref_hit,
            "place_on_site": place_hit[:2]}


def guess(name: str, city: str | None, region: str | None, country: str, approval_ref: str | None,
          fetcher: SafeFetcher, resolver=resolves) -> dict:
    tried = []
    for dom in candidates(name, country):
        if not resolver(dom):
            continue
        tried.append(dom)
        hit = confirm_site(name, f"https://{dom}/", fetcher, approval_ref, (city or "", region or ""))
        if hit:
            return {"status": "found", "method": "guess", **hit}
        if len(tried) >= 4:          # four live domains read and none is the school: stop
            break
    return {"status": "not_found", "method": "guess", "tried": tried}


def tavily_query(name: str, city: str | None, region: str | None, country: str) -> str:
    trading = re.split(r"\b(?:T/A|DBA|d/b/a)\b", name, flags=re.I)[-1].strip()
    from .places import COUNTRY
    where = ", ".join(x for x in (city, region, COUNTRY.get(country, country)) if x)
    return f"{trading} {where} flight school official website"


def tavily(name: str, city: str | None, region: str | None, country: str, approval_ref: str | None,
           fetcher: SafeFetcher, key: str, session=None) -> dict:
    """One basic Tavily search (1 credit); the caller has already reserved it in the quota."""
    import requests
    r = (session or requests).post(TAVILY, timeout=30, headers={"Authorization": f"Bearer {key}"},
                                   json={"query": tavily_query(name, city, region, country), "search_depth": "basic",
                                         "max_results": 8})
    if r.status_code != 200:
        raise RuntimeError(f"Tavily {r.status_code}: {r.text[:200]}")
    seen = []
    for res in (r.json() or {}).get("results") or []:
        dom = domain_of(res.get("url") or "")
        if not dom or dom in seen or NOT_OWN_SITE.search(dom + "."):
            continue
        seen.append(dom)
        url = res["url"]
        root = f"{urlsplit(url).scheme or 'https'}://{urlsplit(url).hostname}/"
        hit = confirm_site(name, root, fetcher, approval_ref, (city or "", region or "")) or \
            (confirm_site(name, url, fetcher, approval_ref, (city or "", region or "")) if url.rstrip("/") != root.rstrip("/") else None)
        if hit:
            return {"status": "found", "method": "tavily", **hit}
        if len(seen) >= 4:
            break
    return {"status": "not_found", "method": "tavily", "tried": seen}
