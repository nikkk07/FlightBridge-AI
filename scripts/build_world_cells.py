"""Build flightbridge/config/world_cells.json: the 1-degree map squares the world sweep searches, country by country.

Flying schools sit at airfields, so a square is searched only if it holds at least one airfield
(OurAirports, public domain: https://ourairports.com/data/ - types small/medium/large_airport; heliports,
seaplane bases, balloon ports and closed fields are left out). Re-run when the data needs a refresh:
    python3 scripts/build_world_cells.py airports.csv
"""
import csv
import json
import math
import pathlib
import sys

# Main training markets first (owner's choice, 2026-11-04), then Europe, Asia, Middle East, Latin America, Africa;
# every other country follows, biggest (most squares) first.
ORDER = ("US CA AU NZ GB IE ZA "
         "DE FR ES IT NL BE PT CH AT SE NO DK FI PL CZ HU GR RO HR SI SK BG LT LV EE LU MT CY IS "
         "PH MY SG TH ID JP KR VN TW HK LK NP BD PK IN "
         "AE SA QA BH OM KW JO IL TR EG "
         "MX BR AR CL CO PE EC UY PY BO CR PA DO "
         "KE NG GH ET MA TN NA BW ZW ZM TZ UG RW MU").split()
SKIP = {"AQ"}


def build(path: str) -> dict:
    cells: dict[str, set] = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["type"] not in ("small_airport", "medium_airport", "large_airport") or r["iso_country"] in SKIP:
                continue
            c = r["iso_country"]
            if len(c) != 2:
                continue
            cells.setdefault(c, set()).add((math.floor(float(r["latitude_deg"])), math.floor(float(r["longitude_deg"]))))
    rest = sorted((c for c in cells if c not in ORDER), key=lambda c: (-len(cells[c]), c))
    order = [c for c in ORDER if c in cells] + rest
    return {"_source": "OurAirports airports.csv (public domain), 1-degree squares holding a small/medium/large airport",
            "order": order, "cells": {c: sorted(cells[c]) for c in order}}


if __name__ == "__main__":
    out = build(sys.argv[1])
    dest = pathlib.Path(__file__).resolve().parents[1] / "flightbridge" / "config" / "world_cells.json"
    dest.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out['order'])} countries, {sum(len(v) for v in out['cells'].values())} squares -> {dest}")
