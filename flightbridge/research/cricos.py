"""Australia: CRICOS — the official register of providers allowed to enrol student-visa holders, with their courses.

Source: data.gov.au dataset "cricos" (Department of Education, CC BY 2.5 AU), monthly XLSX, found through the CKAN API
(https://data.gov.au/data/api/3/action/package_show?id=cricos). Sheets used: Institutions, Courses, Course Locations.
A provider is imported when CRICOS lists an aeroplane CPL course for it (helicopter-only providers are left out).
"""
from __future__ import annotations

import io
import re

from .registers import RegisterRow

CKAN = "https://data.gov.au/data/api/3/action/package_show?id=cricos"
CPL_AEROPLANE = re.compile(r"Commercial Pilot\s*Licen[cs]e\s*[-–]?\s*Aeroplane|Bachelor of Aviation\s*\(Commercial Pilot|"
                           r"Aviation\s*\(Commercial Pilot\)|\bAVI50222\b", re.I)
HELI = re.compile(r"Helicopter", re.I)
STATES = {"QLD": "Queensland", "NSW": "New South Wales", "VIC": "Victoria", "WA": "Western Australia",
          "SA": "South Australia", "TAS": "Tasmania", "ACT": "Australian Capital Territory", "NT": "Northern Territory"}


def latest_resource(session=None) -> dict:
    import requests
    r = (session or requests).get(CKAN, timeout=30)
    r.raise_for_status()
    res = [x for x in r.json()["result"]["resources"] if (x.get("format") or "").upper() == "XLSX"]
    if not res:
        raise RuntimeError("CRICOS: no XLSX resource on data.gov.au")
    return max(res, key=lambda x: x.get("created") or "")


def _rows(ws):
    it = ws.iter_rows(values_only=True)
    hdr = None
    for r in it:                       # header row is the first row that starts with "CRICOS Provider Code"
        if r and r[0] == "CRICOS Provider Code":
            hdr = [str(c).strip() if c is not None else "" for c in r]
            break
    if hdr is None:
        return []
    return [dict(zip(hdr, r)) for r in it if r and r[0]]


def parse(xlsx_bytes: bytes, source_url: str, version: str) -> list[RegisterRow]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    inst = {r["CRICOS Provider Code"]: r for r in _rows(wb["Institutions"])}
    courses: dict[str, list[str]] = {}
    for c in _rows(wb["Courses"]):
        name = str(c.get("Course Name") or "")
        if CPL_AEROPLANE.search(name + " " + str(c.get("VET National Code") or "")) and not HELI.search(name):
            courses.setdefault(c["CRICOS Provider Code"], []).append(name.strip())
    locs: dict[str, list[tuple]] = {}
    for loc in _rows(wb["Course Locations"]):
        code = loc["CRICOS Provider Code"]
        if code in courses:
            locs.setdefault(code, []).append((str(loc.get("Location City") or "").title(), str(loc.get("Location State") or "")))
    out = []
    for code, cs in courses.items():
        i = inst.get(code) or {}
        name = (i.get("Trading Name") or "").strip() or (i.get("Institution Name") or "").strip()
        if not name:
            continue
        city, state = (locs.get(code) or [((i.get("Postal Address City") or "").title(), i.get("Postal Address State") or "")])[0]
        web = (i.get("Website") or "").strip() or None
        out.append(RegisterRow(name=name, country="AU", authority="CRICOS", classification="CRICOS_PROVIDER",
                               approval_ref=code, source_url=source_url, source_version=version,
                               website=web, city=city or None, checked_by=None,
                               region=STATES.get(state.upper(), state) or None, courses=tuple(sorted(set(cs)))))
    return out
