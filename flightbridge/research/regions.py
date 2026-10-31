"""Region ("state" level) for grouping schools: UK nation from the postcode area, otherwise what the register gives.

UK postcode areas by nation — Royal Mail postcode areas; Scotland, Wales and Northern Ireland areas below, all others England.
Border areas are resolved at district/sector level: CH5-CH8 and sector CH4 0 (Flintshire: Hawarden, Broughton, Saltney)
and SY15-SY25 (Powys/Ceredigion/Gwynedd) are Wales; the rest of CH and SY is England.
"""
from __future__ import annotations

import re

SCOTLAND = {"AB", "DD", "DG", "EH", "FK", "G", "HS", "IV", "KA", "KW", "KY", "ML", "PA", "PH", "TD", "ZE"}
WALES = {"CF", "LD", "LL", "NP", "SA"}
NI = {"BT"}
CROWN = {"JE": "Jersey", "GY": "Guernsey", "IM": "Isle of Man"}
POSTCODE = re.compile(r"\b([A-Z]{1,2})(\d[A-Z\d]?) ?(\d)[A-Z]{2}\b")
WALES_DISTRICTS = {f"CH{n}" for n in (5, 6, 7, 8)} | {f"SY{n}" for n in range(15, 26)}
WALES_SECTORS = {"CH4 0"}


def uk_nation(address_lines) -> str | None:
    for line in reversed(list(address_lines or [])):
        m = POSTCODE.search(line.upper())
        if m:
            area, district, sector = m.group(1), m.group(1) + m.group(2), f"{m.group(1)}{m.group(2)} {m.group(3)}"
            if district in WALES_DISTRICTS or sector in WALES_SECTORS:
                return "Wales"
            if area in CROWN:
                return CROWN[area]
            if area in SCOTLAND:
                return "Scotland"
            if area in WALES:
                return "Wales"
            if area in NI:
                return "Northern Ireland"
            return "England"
    text = " ".join(address_lines or [])
    for n in ("Scotland", "Wales", "Northern Ireland", "England"):
        if n in text:
            return n
    return None
