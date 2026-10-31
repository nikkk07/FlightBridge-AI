"""Parser for UK CAA Standards Document 31 (Approved Training Organisations), text from `pdftotext -layout`.

Each entry: "<Name> (GBR.ATO.nnnn[L])" (name may wrap onto the previous line), "Registered Company Number: ...",
address lines, phone, then "•" course bullets (which may wrap). Page headers/footers are stripped.
Source: https://www.caa.co.uk/publication/download/13488 (SD31; version and date read from the document itself).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

REF_RE = re.compile(r"\((GBR\.(?:ATO|LTB)\.\d{3,4}[A-Z]?)\)\s*$")
NOISE = [re.compile(p) for p in (
    r"OFFICIAL - Public", r"^\s*Approved Training Organisations Doc\. 31", r"Page \d+ of \d+\s*$")]
VERSION_RE = re.compile(r"Version\s+(\d+),\s+(\d{1,2} \w+ \d{4})")
COMPANY_RE = re.compile(r"Registered Company Number:\s*(.+)$")
COUNTRY_IN_COMPANY = re.compile(r"\(([A-Za-z ]+)\)\s*$")
PHONE_RE = re.compile(r"^[+()\d][\d ()+\-]{6,}$")
COUNTRY_ISO = {"France": "FR", "Ireland": "IE", "USA": "US", "Spain": "ES", "Cyprus": "CY", "Malta": "MT",
               "Netherlands": "NL", "Germany": "DE", "Switzerland": "CH", "Greece": "GR", "Portugal": "PT",
               "Poland": "PL", "Belgium": "BE", "Italy": "IT", "Austria": "AT", "Sweden": "SE", "Denmark": "DK",
               "Norway": "NO", "Canada": "CA", "Australia": "AU", "South Africa": "ZA", "Czech Republic": "CZ",
               "Hungary": "HU", "Lithuania": "LT", "Latvia": "LV", "Estonia": "EE", "Slovakia": "SK",
               "Slovenia": "SI", "Croatia": "HR", "Romania": "RO", "Bulgaria": "BG", "United Arab Emirates": "AE",
               "UAE": "AE", "Jersey": "JE", "Guernsey": "GG", "Isle of Man": "IM", "Finland": "FI",
               "Brunei Darussalam": "BN", "Brunei": "BN", "Luxembourg": "LU", "Iceland": "IS", "Turkey": "TR",
               "Singapore": "SG", "Qatar": "QA", "Saudi Arabia": "SA", "India": "IN", "New Zealand": "NZ"}
UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b")
UK_PARTS = ("Northern Ireland", "Scotland", "Wales", "England", "United Kingdom")


@dataclass
class SD31Entry:
    name: str
    approval_ref: str
    company_number: str | None = None
    registered_country: str | None = None   # from "(France)" etc. after the company number; None => UK
    address: list = field(default_factory=list)
    phone: str | None = None
    courses: list = field(default_factory=list)

    @property
    def country(self) -> str:
        """Where the school is (address), not where the company is registered."""
        for line in self.address[::-1]:
            if any(p in line for p in UK_PARTS):
                return "GB"
            for k, v in COUNTRY_ISO.items():
                if re.search(rf"\b{re.escape(k)}\b", line):
                    return v
        if any(UK_POSTCODE.search(line) for line in self.address):
            return "GB"
        if self.registered_country:
            return COUNTRY_ISO.get(self.registered_country, self.registered_country)
        return "GB"

    def has(self, pattern: str) -> bool:
        return any(re.search(pattern, c, re.I) for c in self.courses)

    @property
    def cpl_aeroplane_flying(self) -> bool:
        # CPL(A) flying courses (modular or integrated). Theory-only courses say "Theoretical Knowledge".
        return any(re.search(r"Commercial Pilot Licence \(Aeroplanes\)", c) and "Theoretical Knowledge" not in c
                   for c in self.courses) or self.has(r"Airline Transport Pilot Licence \(Aeroplanes\) Integrated")

    @property
    def ppl_aeroplane(self) -> bool:
        return self.has(r"Private Pilot Licence \(Aeroplanes\)")

    @property
    def integrated_atpl(self) -> bool:
        return self.has(r"Airline Transport Pilot Licence \(Aeroplanes\) Integrated")

    @property
    def instrument_rating(self) -> bool:
        return self.has(r"Instrument Rating \(Aeroplanes\)")


def clean_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if not any(rx.search(ln) for rx in NOISE)]


def document_version(text: str) -> tuple[str | None, str | None]:
    m = VERSION_RE.search(text)
    return (m.group(1), m.group(2)) if m else (None, None)


def parse(text: str) -> list[SD31Entry]:
    lines = clean_lines(text)
    entries: list[SD31Entry] = []
    cur: SD31Entry | None = None
    in_courses = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        m = REF_RE.search(line)
        if m and not line.startswith("•"):
            name = line[:m.start()].strip()
            prev = lines[i - 1].strip() if i >= 1 else ""
            prev2 = lines[i - 2].strip() if i >= 2 else ""
            # A wrapped name: the line just above is non-empty, not a course bullet, and starts a block.
            if prev and not prev.startswith("•") and not prev2:
                name = (prev + " " + name).strip()
                if cur and cur.address and cur.address[-1] == prev:
                    cur.address.pop()
                if cur and cur.courses and cur.courses[-1].endswith(" " + prev):
                    cur.courses[-1] = cur.courses[-1][: -len(prev) - 1]
            cur = SD31Entry(name=re.sub(r"\s+", " ", name), approval_ref=m.group(1))
            entries.append(cur)
            in_courses = False
            continue
        if cur is None or not line:
            continue
        cm = COMPANY_RE.match(line)
        if cm:
            cur.company_number = cm.group(1).strip()
            cc = COUNTRY_IN_COMPANY.search(cur.company_number)
            if cc and not re.search(r"\d", cc.group(1)):
                cur.registered_country = cc.group(1).strip()
            continue
        if line.startswith("•"):
            in_courses = True
            cur.courses.append(re.sub(r"\s+", " ", line.lstrip("•").strip()))
            continue
        if in_courses and raw.startswith("         ") and cur.courses:   # wrapped bullet continuation
            cur.courses[-1] += " " + line
            continue
        if not in_courses:
            if PHONE_RE.match(line) and cur.phone is None:
                cur.phone = line
            else:
                cur.address.append(line)
    # A trailing line that is actually the next entry's wrapped name was popped above; drop empty names.
    return [e for e in entries if e.name]
