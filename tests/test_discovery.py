"""Country > State > Area discovery, per-country eligibility, CRICOS parsing, phone numbers."""
import io

import psycopg
import pytest

from flightbridge import automation, eligibility, secrets
from flightbridge.research import cricos, discover
from flightbridge.research.pipeline import import_rows
from flightbridge.research.registers import validate_row
from flightbridge.settings import Settings
from flightbridge.worker import run_once


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setenv("SECRET_KEY", "s")
    secrets.configure(None)
    yield
    secrets.configure(None)


def place(pid, name, web=None, lat=28.78, lng=-81.24, phone="+1 407-555-0100", status="OPERATIONAL", country="US",
          state="Florida", city="Sanford"):
    return {"id": pid, "displayName": {"text": name}, "websiteUri": web, "location": {"latitude": lat, "longitude": lng},
            "internationalPhoneNumber": phone, "businessStatus": status, "types": ["school"],
            "addressComponents": [{"types": ["country"], "shortText": country, "longText": country},
                                  {"types": ["administrative_area_level_1"], "longText": state, "shortText": state[:2]},
                                  {"types": ["locality"], "longText": city, "shortText": city}]}


class Resp:
    def __init__(self, places):
        self.status_code, self._p = 200, places

    def json(self):
        return {"places": self._p}


class Session:
    def __init__(self, places):
        self.places, self.bodies = places, []

    def post(self, url, json=None, **kw):
        self.bodies.append(json)
        return Resp(self.places)


def area_id(url, name="Sanford (Orlando)"):
    with psycopg.connect(url) as c:
        return c.execute("SELECT id FROM flightbridge.area WHERE name=%s", (name,)).fetchone()[0]


# ---------------------------------------------------------------- eligibility rules
@pytest.mark.parametrize("country,reg,signals,pages,closed,expected", [
    ("AU", [{"classification": "CRICOS_PROVIDER", "status": "listed_in_register", "authority_code": "CRICOS", "approval_ref": "01552D", "source_url": "u"}], {}, 0, False, "eligible_verified"),
    ("US", [], {"part141": True, "aeroplane": True}, 5, False, "eligible_claimed"),
    ("US", [], {"aeroplane": True}, 6, False, "unqualified"),
    ("US", [], {}, 0, False, "unknown"),
    ("NZ", [], {"helicopter": True, "caa_nz_part141": True}, 4, False, "unqualified"),
    ("CA", [], {"drone": True}, 3, False, "unqualified"),
    ("CA", [], {"transport_canada": True, "aeroplane": True}, 3, True, "unqualified"),
    ("US", [{"classification": "PART141_FAA", "status": "verified_by_human", "authority_code": "FAA", "approval_ref": None, "source_url": "u"}], {}, 0, False, "eligible_verified"),
    # countries with no register (owner's decision 2026-11-04): read website -> "Check needed" (eligible_claimed)
    ("IN", [], {"part141": True}, 3, False, "eligible_claimed"),
    ("ZA", [], {"aeroplane": True}, 2, False, "eligible_claimed"),
    ("ZA", [], {}, 0, False, "unknown"),
    ("ZA", [], {"helicopter": True}, 2, False, "unqualified"),
    ("ZA", [], {"aeroplane": True}, 2, True, "unqualified"),
    ("ZA", [{"classification": "NATIONAL_APPROVAL", "status": "verified_by_human", "authority_code": "NATIONAL_CAA", "approval_ref": "ATO 123", "source_url": "u"}], {}, 2, False, "eligible_verified"),
    ("IN", [{"classification": "FTO_DGCA", "status": "listed_in_register", "authority_code": "DGCA", "approval_ref": None, "source_url": "u"}], {}, 2, False, "eligible_claimed"),
])
def test_eligibility_rules(country, reg, signals, pages, closed, expected):
    decision, reasons = eligibility.evaluate(country, reg, {"signals": signals, "evidence": []}, pages, closed)
    assert decision == expected and reasons and all("text" in r for r in reasons)


def test_claim_reasons_quote_the_website():
    d, reasons = eligibility.evaluate("US", [], {"signals": {"sevp": True}, "evidence": [
        {"signal": "sevp", "url": "https://x.test/intl", "excerpt": "We are SEVP certified and issue I-20s"}]}, 3)
    assert d == "eligible_claimed" and reasons[0]["excerpt"].startswith("We are SEVP") and reasons[0]["source_url"] == "https://x.test/intl"


# ---------------------------------------------------------------- CRICOS
def _cricos_xlsx():
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Purpose Statement"
    def sheet(name, hdr, rows):
        ws = wb.create_sheet(name); ws.append([name]); ws.append(["Report generated"]); ws.append(hdr)
        for r in rows: ws.append(r)
    sheet("Institutions", ["CRICOS Provider Code", "Trading Name", "Institution Name", "Institution Type", "Institution Capacity", "Website",
                           "Postal Address Line 1", "Postal Address Line 2", "Postal Address Line 3", "Postal Address Line 4",
                           "Postal Address City", "Postal Address State", "Postal Address Postcode"],
          [["01552D", "Basair Aviation College", "Basair Pty Ltd", "Private", 300, "www.basair.edu.au", "", "", "", "", "BANKSTOWN", "NSW", "2200"],
           ["09999H", "", "Heli Only Pty Ltd", "Private", 20, "heli.example", "", "", "", "", "CAIRNS", "QLD", "4870"]])
    sheet("Courses", ["CRICOS Provider Code", "Institution Name", "CRICOS Course Code", "Course Name", "VET National Code"],
          [["01552D", "Basair", "1", "Diploma of Aviation (Commercial Pilot Licence - Aeroplane)", "AVI50222"],
           ["09999H", "Heli", "2", "Diploma of Aviation (Commercial Pilot Licence - Helicopter)", "AVI50519"]])
    sheet("Course Locations", ["CRICOS Provider Code", "Institution Name", "CRICOS Course Code", "Location Name", "Location City", "Location State"],
          [["01552D", "Basair", "1", "Bankstown campus", "BANKSTOWN", "NSW"]])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def test_cricos_keeps_only_aeroplane_cpl_providers_and_becomes_verified(clean_db):
    rows = cricos.parse(_cricos_xlsx(), "https://data.gov.au/x.xlsx", "CRICOS export 2026-10-01")
    assert [(r.approval_ref, r.name, r.region, r.city, r.website_domain) for r in rows] == [
        ("01552D", "Basair Aviation College", "New South Wales", "Bankstown", "basair.edu.au")]
    import_rows(clean_db, rows)
    assert discover.assign_areas(clean_db) == 1                      # Bankstown -> "Sydney (Bankstown)"
    with psycopg.connect(clean_db) as c:
        oid = c.execute("SELECT id FROM flightbridge.organisation").fetchone()[0]
        assert eligibility.evaluate_org(c, oid) == "eligible_verified"


# ---------------------------------------------------------------- area discovery
def test_area_search_creates_dedupes_filters_and_stores_google_phone(clean_db):
    aid = area_id(clean_db)
    s = Session([place("p1", "Sanford Flight Academy", "https://www.sfa.example/"),
                 place("p2", "Airport Diner Cafe"),                                   # not a school
                 place("p3", "Faraway Flight School", lat=40.7, lng=-74.0),            # outside the area
                 place("p4", "Old Aero Club", status="CLOSED_PERMANENTLY")])
    out = discover.search_area(clean_db, aid, session=s)
    assert (out["new"], out["skipped"], out["closed"]) == (2, 2, 1)
    assert s.bodies[0]["locationBias"]["circle"]["radius"] == 40000.0
    again = discover.search_area(clean_db, aid, session=s)               # second run: nothing duplicated
    assert again["new"] == 0 and again["matched"] == 2
    with psycopg.connect(clean_db) as c:
        rows = c.execute("""SELECT o.name, o.region, o.city, o.website_domain, a.name FROM flightbridge.organisation o
                            JOIN flightbridge.area a ON a.id=o.area_id ORDER BY o.name""").fetchall()
        phones = c.execute("SELECT phone, source FROM flightbridge.contact_phone").fetchall()
        seen = c.execute("SELECT name, is_new FROM flightbridge.detection ORDER BY id").fetchall()
    assert rows[1] == ("Sanford Flight Academy", "Florida", "Sanford", "sfa.example", "Sanford (Orlando)")
    assert seen[:2] == [("Sanford Flight Academy", True), ("Old Aero Club", True)] and seen[2:] == [
        ("Sanford Flight Academy", False), ("Old Aero Club", False)]                  # each search logged for the globe
    assert ("+14075550100", "google_places") in phones


def test_area_search_matches_register_school_by_website_and_respects_storage_switch(clean_db):
    import_rows(clean_db, [validate_row({"name": "Basair Aviation College", "country": "AU", "authority": "CRICOS",
                                         "classification": "CRICOS_PROVIDER", "approval_ref": "01552D",
                                         "source_url": "https://data.gov.au/x", "website": "www.basair.edu.au",
                                         "region": "New South Wales", "city": "Bankstown"})])
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('integrations', '{"store_google_contacts": false}')
                     ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""")
    secrets.configure(None)
    aid = area_id(clean_db, "Sydney (Bankstown)")
    out = discover.search_area(clean_db, aid, session=Session([place("g1", "Basair", "https://basair.edu.au/", lat=-33.92, lng=150.99,
                                                                     country="AU", state="New South Wales", city="Bankstown")]))
    assert out["matched"] == 1 and out["new"] == 0 and out["phones"] == 0
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT count(*) FROM flightbridge.organisation").fetchone()[0] == 1
        assert c.execute("SELECT area_id FROM flightbridge.organisation").fetchone()[0] == aid
        assert c.execute("SELECT count(*) FROM flightbridge.contact_phone").fetchone()[0] == 0


def test_run_now_search_area_job_and_no_draft_until_verified(clean_db, monkeypatch):
    aid = area_id(clean_db)
    monkeypatch.setattr(discover, "places_search_area", lambda area, q, **k: [place("p9", "Orlando Pilot Academy", "https://opa.example/")] if q == "flight school" else [])

    class F:
        def get(self, url):
            from flightbridge.research.fetch import Page
            html = {"https://opa.example": "<p>FAA Part 141 approved. Cessna 172. Email <a href='mailto:info@opa.example'>info@opa.example</a> "
                                           "<a href='tel:+14075551234'>call</a></p>"}.get(url.rstrip("/"))
            return Page(url, 200 if html else 404, html or "", True)
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("INSERT INTO flightbridge.job_request (kind, area_id) VALUES ('search_area', %s)", (aid,))
    out = run_once(Settings(database_url=clean_db), reader=None, fetcher=F(), trigger="queue")
    job = out["jobs"][0]
    assert job["discover"]["new"] == 1 and job["research"]["emails_found"] == 1 and job["eligibility"]["eligible_claimed"] == 1
    with psycopg.connect(clean_db) as c:
        org = c.execute("SELECT eligibility, eligibility_reasons->0->>'text' FROM flightbridge.organisation").fetchone()
        phones = {r[0] for r in c.execute("SELECT source FROM flightbridge.contact_phone").fetchall()}
        drafts = c.execute("SELECT count(*) FROM flightbridge.ai_draft").fetchone()[0]
    assert org == ("eligible_claimed", "Website says FAA Part 141") and {"website", "google_places"} <= phones
    assert drafts == 0                     # claimed is not enough: a person confirms before any outreach


def test_uk_register_phone_is_saved_as_regulator_source(clean_db):
    import_rows(clean_db, [validate_row({"name": "Aeros", "country": "GB", "authority": "UK_CAA", "classification": "ATO_UK",
                                         "approval_ref": "GBR.ATO.0105", "source_url": "https://www.caa.co.uk/x",
                                         "phone": "02476 305 450"})])
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT phone, source FROM flightbridge.contact_phone").fetchone() == ("02476305450", "regulator")


def test_person_decisions_stick(clean_db):
    with psycopg.connect(clean_db, autocommit=True) as c:
        emp = c.execute("INSERT INTO public.employee (name) VALUES ('R') RETURNING id").fetchone()[0]
        oid = c.execute("INSERT INTO flightbridge.organisation (name, country, status) VALUES ('Orlando Air','US','research_completed') RETURNING id").fetchone()[0]
        eligibility.mark_unqualified(c, oid, emp, "Part 61 only")
        assert eligibility.evaluate_org(c, oid) == "unqualified"            # automation doesn't overwrite it
        assert eligibility.confirm(c, oid, emp, "ABCD123X", "https://www.faa.gov/x") == "eligible_verified"
        st, ref = c.execute("SELECT status, approval_ref FROM flightbridge.regulatory_approval WHERE org_id=%s", (oid,)).fetchone()
    assert (st, ref) == ("verified_by_human", "ABCD123X")
