"""Is this school one that an official list already shows under another name?

Official lists use the legal name ("CLOUD DANCER AVIATION INC", "ANSON AIR LLC"); Google and websites use the trading
name ("CDA Flight Academy", "Anson Aviation"). When a list is imported, schools it doesn't recognise are added as new
records, so the same school can exist twice. Before a school is called "not on the list", this looks for a listed
school in the same state that is very likely the same one:

  strong (the listed credential is copied to this school, reason recorded):
    * the listed approval number appears on this school's website
    * the listed name (without Inc/LLC) appears on this school's website, or is the legal name the AI read there
    * the website address is the listed name run together (ansonair.com <- "Anson Air")
    * same town, and the same distinctive words (or 2+ words of one name all in the other; place words don't count)
  weak ("Check needed: looks like X, confirm"):
    * same town, and the initials of the listed name match this school's name or web address (CDA <- Cloud Dancer Aviation)
"""
from __future__ import annotations

import re

LEGAL = re.compile(r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|pty|plc|dba|d/b/a|the|lp|llp|pllc)\b\.?",
                   re.I)
GENERIC = {"aviation", "flight", "flying", "school", "academy", "training", "center", "centre", "club", "aero", "services",
           "service", "pilot", "pilots", "college", "institute", "international", "group", "enterprises", "and", "of",
           "air", "airways", "aeronautical", "aerospace", "university", "inc", "llc"}


def clean(name: str) -> str:
    s = re.split(r"\b(?:T/A|DBA|d/b/a)\b", name or "", flags=re.I)
    s = LEGAL.sub(" ", " ".join(s).lower().replace("&", " and "))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def words(name: str, places: set[str]) -> set[str]:
    return {w for w in clean(name).split() if len(w) > 2 and w not in GENERIC and w not in places}


def initials(name: str) -> str:
    return "".join(w[0] for w in clean(name).split() if w not in ("and", "of", "the"))


def place_words(*vals) -> set[str]:
    return {w for v in vals if v for w in re.findall(r"[a-z]+", v.lower())}


def match(org: dict, candidates: list[dict], texts: list[str] = (), legal_names: list[str] = ()) -> tuple[str, dict, str] | None:
    """org: {name, city, region, domain}; candidates: listed schools [{id, name, city, ref, list, source_url}].
    Returns (strength, candidate, how) for the best match, or None."""
    low = " ".join(t.lower() for t in texts)
    page = " " + re.sub(r"[^a-z0-9]+", " ", low) + " " if low else ""
    places = place_words(org.get("city"), org.get("region"))
    mine = words(org["name"], places)
    label = (org.get("domain") or "").lower().split(".")[0].removeprefix("www")
    legal = {clean(n) for n in legal_names if n and len(clean(n)) >= 6}
    weak = None
    for c in candidates:
        cn = clean(c["name"])
        same_town = bool(org.get("city") and c.get("city") and org["city"].strip().lower() == c["city"].strip().lower())
        if c.get("ref") and len(c["ref"]) >= 5 and c["ref"].lower() in low:
            return "strong", c, f"its approval number {c['ref']} is on the school's website"
        if len(cn) >= 8 and (cn in legal or (page and f" {cn} " in page)):
            return "strong", c, "the listed name is on the school's website"
        joined = cn.replace(" ", "")
        if label and len(joined) >= 6 and (label == joined or label == "fly" + joined):
            return "strong", c, f"the web address {org.get('domain')} is the listed name"
        theirs = words(c["name"], places | place_words(c.get("city")))
        if same_town and mine and theirs and (mine == theirs or (min(len(mine), len(theirs)) >= 2
                                                                and (mine <= theirs or theirs <= mine))):
            return "strong", c, "same town and the same name"
        ini = initials(c["name"])
        if weak is None and same_town and len(ini) >= 3:
            toks = set(clean(org["name"]).split())
            if ini in toks or (label and (label.startswith(ini) or label.endswith(ini) or label.startswith("fly" + ini))):
                weak = (c, f"same town, initials {ini.upper()}")
    if weak:
        return "weak", weak[0], weak[1]
    return None
