# Step 1 — Finding approved foreign flying schools

Rule: a school enters the pipeline only from an **official regulator source**. Google, directories and the school's own website are used later, to find contact details, never to prove approval.

## Status by country (checked 2026-10-31)

| # | Country | Regulator source | Can it be automated? | Status |
|---|---|---|---|---|
| 1a | United Kingdom | UK CAA Standards Document 31, **v222, 31 Oct 2026** (PDF, "cleared for unrestricted distribution") | Yes: PDF download + parser (`flightbridge/research/registers_uk.py`) | **Done.** 196 approved training organisations; **35 run CPL (Aeroplanes) flying courses** (34 in the UK, 1 in Spain: FTE Jerez). Files in `data/registers/uk_caa/` |
| 1b | USA | FAA Facility Dashboard (Tableau on explore.dot.gov) | **No.** The data export returns 403 Access Denied; the FAA publishes no current list (AC 140-2 was discontinued) | Person checks each candidate in the dashboard, then records the certificate number |
| 1c | Canada | Transport Canada Flight Training Units Search | **No.** `wwwapps.tc.gc.ca/robots.txt` = `Disallow: /` | Person checks each candidate (filter: CPL(A) Integrated / Commercial Pilot) |
| 1d | Australia | CASA "Find a flight training operator" | **No.** Robots-disallowed and unreachable from our network | Person checks; dedupe on CASA ARN |
| 1e | EU / EEA | Each national authority (e.g. Spain AESA, Ireland IAA, Malta TM CAD, Poland ULC) | Varies per country | Next: pick countries, then check each authority's published list |

## How the UK list was built (reproducible)

1. Download SD31 from https://www.caa.co.uk/publication/download/13488 (SHA-256 of v222 PDF: `826d4f3e3521c54e97bc91c33500cdfc73845401ac17fb4d85d915fa88b5e291`).
2. `pdftotext -layout` → `SD31_v222_2026-10-31.txt`.
3. `python3 scripts/import_uk_sd31.py data/registers/uk_caa/SD31_v222_2026-10-31.txt` writes:
   - `*_all.csv`: all 196 organisations with course flags (CPL flying, integrated ATPL, PPL, IR)
   - `*_register.csv`: the 35 CPL(A) flying schools, ready for `python -m flightbridge.cli import-register`
4. A school counts as CPL(A) flying training when a listed course names "Commercial Pilot Licence (Aeroplanes)" and is not theory-only, or names an integrated ATPL(A). Location comes from the school's address, not the company registration.
5. Tests: `tests/test_registers_uk.py` (sample fixture + regression on v222).

## Not yet known for the 35 UK schools (next steps)

- **Website and public email**: SD31 lists address and phone only.
- **International students accepted**: read from each school's own website.
- **Human review** of entries that are clearly not referral partners (e.g. Empire Test Pilots School, Wings for Warriors).
