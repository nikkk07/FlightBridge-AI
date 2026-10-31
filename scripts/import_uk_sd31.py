"""Step 1a — UK CAA approved training organisations (Standards Document 31).

Usage:
  python3 scripts/import_uk_sd31.py data/registers/uk_caa/SD31_v222_2026-10-31.txt
Writes next to the input:
  *_all.csv         every ATO with course flags (evidence table)
  *_register.csv    CPL(Aeroplane) flying-training ATOs in the import-register format
                    (python -m flightbridge.cli import-register <file>)
"""
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from flightbridge.research.registers_uk import document_version, parse  # noqa: E402

SOURCE_URL = "https://www.caa.co.uk/publication/download/13488"

src = pathlib.Path(sys.argv[1])
text = src.read_text(encoding="utf-8")
ver, date = document_version(text)
if not ver:
    sys.exit("could not read the SD31 version/date from the document")
entries = parse(text)
version_label = f"SD31 v{ver} ({date})"
stem = src.with_suffix("")

with open(f"{stem}_all.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["approval_ref", "name", "location_country", "company_number", "address", "phone",
                "cpl_aeroplane_flying", "integrated_atpl", "ppl_aeroplane", "instrument_rating", "course_count", "courses",
                "source_url", "source_version"])
    for e in entries:
        w.writerow([e.approval_ref, e.name, e.country, e.company_number or "", " | ".join(e.address), e.phone or "",
                    e.cpl_aeroplane_flying, e.integrated_atpl, e.ppl_aeroplane, e.instrument_rating, len(e.courses),
                    " | ".join(e.courses), SOURCE_URL, version_label])

cpl = [e for e in entries if e.cpl_aeroplane_flying]
with open(f"{stem}_register.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["name", "country", "authority", "classification", "approval_ref", "source_url",
                                       "source_version", "website", "city", "checked_by"])
    w.writeheader()
    for e in cpl:
        city = next((a for a in reversed(e.address[:-1]) if not any(ch.isdigit() for ch in a)), "")
        w.writerow({"name": e.name, "country": e.country, "authority": "UK_CAA", "classification": "ATO_UK",
                    "approval_ref": e.approval_ref, "source_url": SOURCE_URL, "source_version": version_label,
                    "website": "", "city": city, "checked_by": ""})

print(f"{version_label}: {len(entries)} organisations; {len(cpl)} with CPL(Aeroplane) flying courses")
print(f"wrote {stem}_all.csv and {stem}_register.csv")
