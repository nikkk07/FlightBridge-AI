"""Read an official school list a person downloaded from a regulator's website and uploaded in the CRM.

Some regulators publish their approved-school lists only through a search page or dashboard that refuses automated
downloads, so a person downloads (or saves) the list and uploads it in CRM > FlightBridge AI > Settings > Official lists.
Each KIND below says which country, authority and approval the list proves, and where it comes from.

Accepted files: CSV (UTF-8 or UTF-16, comma or tab separated, as Tableau exports), Excel .xlsx, and a saved web page
(.html/.htm: every <table> on the page is read). The column names of these exports are not published, so columns are
found by their header words:
  name — a header containing "name";  city — "city";  state/province — "state" or "province"
  reference — "certificate", "designator", "dli", "arn", "number", "cert" or "id"
  type — "type", "part", "program", "category" (used by the kind's row filter when present)
"""
from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser

from .registers import RegisterRow

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
CA_PROVINCES = {"AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
                "NS": "Nova Scotia", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan",
                "NT": "Northwest Territories", "NU": "Nunavut", "YT": "Yukon"}
AU_STATES = {"QLD": "Queensland", "NSW": "New South Wales", "VIC": "Victoria", "WA": "Western Australia", "SA": "South Australia",
             "TAS": "Tasmania", "ACT": "Australian Capital Territory", "NT": "Northern Territory"}
AVIATION_NAME = re.compile(r"aviation|aero|flight|flying|pilot|air\b|airline|aircraft|helicopter", re.I)

# kind -> what the list proves. 'keep' filters rows (by type column text and/or school name); None keeps every row.
KINDS = {
    "faa_part141": {
        "country": "US", "authority": "FAA", "classification": "PART141_FAA", "states": US_STATES,
        "source_url": "https://www.faa.gov/av-info/facility-dashboard",
        "keep_type": re.compile(r"141|pilot school", re.I)},
    "tc_ftu": {
        "country": "CA", "authority": "TC", "classification": "FTU_TC", "states": CA_PROVINCES,
        "source_url": "https://wwwapps.tc.gc.ca/Saf-Sec-Sur/2/CAS-SAC/ftaefveas.aspx?lang=eng"},
    "ircc_dli": {
        "country": "CA", "authority": "IRCC_DLI", "classification": "DLI_CA", "states": CA_PROVINCES,
        "source_url": "https://www.canada.ca/en/immigration-refugees-citizenship/services/study-canada/study-permit/prepare/designated-learning-institutions-list.html",
        "keep_name": AVIATION_NAME},          # the DLI list covers every college; keep the aviation ones
    "casa_ato": {
        "country": "AU", "authority": "CASA", "classification": "PART141_CASA", "states": AU_STATES,
        "source_url": "https://www.casa.gov.au/search-centre/flight-training-operators",
        "class_142": re.compile(r"142", re.I)},
}


class UploadError(ValueError):
    pass


class _Tables(HTMLParser):
    """Every <table> on a saved web page, as rows of cell text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._row, self._cell, self._depth = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            self.tables.append([])
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row) and self.tables:
                self.tables[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[1:2] == b"\x00":
        return data.decode("utf-16")
    return data.decode("utf-8-sig", errors="replace")


def _table(data: bytes, filename: str) -> list[list[str]]:
    name = (filename or "").lower()
    if data[:2] == b"PK" or name.endswith(".xlsx"):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        return [["" if c is None else str(c).strip() for c in row] for row in ws.iter_rows(values_only=True)]
    text = _decode(data)
    if name.endswith((".html", ".htm")) or re.search(r"<table\b", text[:200000], re.I):
        p = _Tables()
        p.feed(text)
        # all tables that have a "name" header, joined (a saved results page may hold several)
        rows = []
        for t in p.tables:
            hi = next((i for i, r in enumerate(t[:5]) if any("name" in c.lower() for c in r)), None)
            if hi is not None:
                rows += t[hi:] if not rows else t[hi + 1:]
        return rows
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


def parse(kind: str, data: bytes, filename: str, uploaded_by: str | None, uploaded_at: str) -> tuple[list[RegisterRow], dict]:
    """-> (rows, report). Raises UploadError when the kind is unknown or no name column is found."""
    k = KINDS.get(kind)
    if not k:
        raise UploadError(f"Unknown list type: {kind}")
    table = [r for r in _table(data, filename) if any(c for c in r)]
    hi = next((i for i, r in enumerate(table[:20]) if any("name" in c.lower() for c in r)), None)
    if hi is None:
        raise UploadError("No column with 'name' in its heading was found. Download the list as CSV or Excel, "
                          "or save the results page (Web Page, HTML) and upload that.")
    header = table[hi]
    c_name = _col(header, "facility name", "school name", "institution name", "organisation name", "organization name",
                  "operator name", "name")
    c_city = _col(header, "city", "town", "location")
    c_state = _col(header, "state", "province", "territory")
    c_ref = _col(header, "certificate", "designator", "dli", "arn", "number", "cert", " id", "id",
                 avoid=("name", "phone", "zip", "postal", "fax"))
    c_type = _col(header, "type", "part", "program", "category", avoid=("name",))
    rows, seen = [], set()
    skipped = {"not_matching": 0, "no_name": 0, "duplicate": 0}
    for raw in table[hi + 1:]:
        cell = lambda i: (raw[i].strip() if i is not None and i < len(raw) and raw[i] else "")   # noqa: E731
        name = cell(c_name)
        if not name or name.lower() == header[c_name].lower():
            skipped["no_name"] += 1
            continue
        kind_text = cell(c_type)
        if (k.get("keep_type") and c_type is not None and kind_text and not k["keep_type"].search(kind_text)) or \
                (k.get("keep_name") and not k["keep_name"].search(name)):
            skipped["not_matching"] += 1
            continue
        st = cell(c_state)
        region = (k["states"].get(st.upper(), st.title()) if st else None)
        ref = re.sub(r"\s+", "", cell(c_ref)) or None
        key = (ref or "", name.lower(), (region or "").lower())
        if key in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(key)
        cls = "PART142_CASA" if k.get("class_142") and k["class_142"].search(kind_text) else k["classification"]
        rows.append(RegisterRow(
            name=name, country=k["country"], authority=k["authority"], classification=cls, approval_ref=ref,
            source_url=k["source_url"], source_version=f"{filename}, uploaded {uploaded_at}",
            website=None, city=cell(c_city).title() or None, checked_by=uploaded_by, region=region,
            from_register_file=True))
    report = {"kind": kind, "rows": len(rows), "skipped": skipped,
              "columns": {n: header[v] if v is not None else None for n, v in
                          (("name", c_name), ("city", c_city), ("state", c_state), ("reference", c_ref), ("type", c_type))}}
    return rows, report
