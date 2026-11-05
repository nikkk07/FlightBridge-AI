"""Read the FAA Part 141 pilot-school list a person downloaded from the FAA facility dashboard and uploaded in the CRM.

Why a person downloads it: the FAA publishes the list only through its facility dashboard
(https://www.faa.gov/av-info/facility-dashboard, linked from https://www.faa.gov/licenses_certificates/airline_certification/pilotschools),
and that site refuses automated downloads. The dashboard offers Download > Crosstab/Data as CSV or Excel.

The exact column names of that export are not published, so the reader finds columns by their header words:
  name  — a header containing "name" (facility / school / certificate holder name)
  city  — "city";  state — "state";  ref — "certificate", "designator", "cert", "number" or "id"
  type  — "type", "part" or "program": when present, only rows mentioning 141 / pilot school are kept
Accepts CSV (UTF-8 or UTF-16, comma or tab separated, as Tableau exports) and .xlsx.
"""
from __future__ import annotations

import csv
import io
import re

from .registers import RegisterRow

SOURCE_URL = "https://www.faa.gov/av-info/facility-dashboard"
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming", "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
}


class UploadError(ValueError):
    pass


def _table(data: bytes, filename: str) -> list[list[str]]:
    if data[:2] == b"PK" or (filename or "").lower().endswith(".xlsx"):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        return [["" if c is None else str(c).strip() for c in row] for row in ws.iter_rows(values_only=True)]
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[1:2] == b"\x00":
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    sample = text[:5000]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _col(header: list[str], *words: str, avoid: tuple = ()) -> int | None:
    for w in words:
        for i, h in enumerate(header):
            hl = h.lower()
            if w in hl and not any(a in hl for a in avoid):
                return i
    return None


def parse(data: bytes, filename: str, uploaded_by: str, uploaded_at: str) -> tuple[list[RegisterRow], dict]:
    """-> (rows, report). Raises UploadError when the file has no recognisable name column."""
    table = [r for r in _table(data, filename) if any(c for c in r)]
    hi = next((i for i, r in enumerate(table[:20]) if any("name" in c.lower() for c in r)), None)
    if hi is None:
        raise UploadError("No column with 'name' in its heading was found. Download the list as CSV or Excel from the FAA dashboard.")
    header = table[hi]
    c_name = _col(header, "facility name", "school name", "name")
    c_city = _col(header, "city")
    c_state = _col(header, "state")
    c_ref = _col(header, "certificate", "designator", "cert", "number", " id", "id", avoid=("name", "phone", "zip"))
    c_type = _col(header, "type", "part", "program", avoid=("name",))
    rows, skipped, seen = [], {"not_part141": 0, "no_name": 0, "duplicate": 0}, set()
    for raw in table[hi + 1:]:
        cell = lambda i: (raw[i].strip() if i is not None and i < len(raw) and raw[i] else "")   # noqa: E731
        name = cell(c_name)
        if not name:
            skipped["no_name"] += 1
            continue
        if c_type is not None and cell(c_type) and not re.search(r"141|pilot school", cell(c_type), re.I):
            skipped["not_part141"] += 1
            continue
        st = cell(c_state)
        region = US_STATES.get(st.upper(), st.title() if st else None) if st else None
        ref = re.sub(r"\s+", "", cell(c_ref)) or None
        key = (ref or "", name.lower(), (region or "").lower())
        if key in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(key)
        rows.append(RegisterRow(
            name=name, country="US", authority="FAA", classification="PART141_FAA", approval_ref=ref,
            source_url=SOURCE_URL, source_version=f"FAA facility dashboard export ({filename}), uploaded {uploaded_at}",
            website=None, city=cell(c_city).title() or None, checked_by=uploaded_by, region=region,
            from_register_file=True))
    report = {"rows": len(rows), "skipped": skipped,
              "columns": {k: header[v] if v is not None else None for k, v in
                          (("name", c_name), ("city", c_city), ("state", c_state), ("certificate", c_ref), ("type", c_type))}}
    return rows, report
