"""World sweep (country by country), the Google free-quota meter, and "Check needed" for countries without a register."""
from datetime import datetime, timezone

import psycopg
import pytest
import requests

from flightbridge import eligibility, secrets
from flightbridge.research import quota, sweep
from flightbridge.research.places import PlacesError


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setenv("SECRET_KEY", "s")
    secrets.configure(None)
    yield
    secrets.configure(None)


@pytest.fixture
def sdb(clean_db, monkeypatch):
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("TRUNCATE flightbridge.detection, flightbridge.sweep_cell, flightbridge.sweep_country, flightbridge.google_usage")
        c.execute("DELETE FROM flightbridge.system_setting WHERE key='google_budget'")
    monkeypatch.setattr(sweep, "load_world", lambda: {"order": ["NZ", "AU"],
                                                      "cells": {"NZ": [[-37, 174], [-44, 172]], "AU": [[-34, 151]]}})
    return clean_db


def place(pid, name, lat=-36.8, lng=174.7, country="NZ", types=("school",), status="OPERATIONAL"):
    return {"id": pid, "displayName": {"text": name}, "location": {"latitude": lat, "longitude": lng},
            "businessStatus": status, "types": list(types),
            "addressComponents": [{"types": ["country"], "shortText": country, "longText": country},
                                  {"types": ["administrative_area_level_1"], "longText": "Auckland", "shortText": "AUK"},
                                  {"types": ["locality"], "longText": "Auckland", "shortText": "Auckland"}]}


class Resp:
    def __init__(self, body, code=200):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


class FakeGoogle:
    """Answers each search from a dict keyed by (lat0, lng0) of the square; records every request body."""
    def __init__(self, pages_by_square):
        self.pages, self.calls = pages_by_square, []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"url": url, "body": json, "mask": headers["X-Goog-FieldMask"]})
        low = json["locationRestriction"]["rectangle"]["low"]
        pages = self.pages.get((low["latitude"], low["longitude"]), [[]])
        i = int(json.get("pageToken") or 0)
        body = {"places": pages[i] if i < len(pages) else []}
        if i + 1 < len(pages):
            body["nextPageToken"] = str(i + 1)
        return Resp(body)


def q(url, sql, *args):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


# ---------------------------------------------------------------- quota meter
@pytest.mark.parametrize("method,url,mask,sku", [
    ("POST", "https://places.googleapis.com/v1/places:searchText", "places.id", None),
    ("POST", "https://places.googleapis.com/v1/places:searchText", "places.id,nextPageToken", None),
    ("POST", "https://places.googleapis.com/v1/places:searchText", sweep.MASK, "text_search_pro"),
    ("POST", "https://places.googleapis.com/v1/places:searchText", "places.location", "text_search_pro"),
    ("POST", "https://places.googleapis.com/v1/places:searchText", "places.id,places.displayName,places.websiteUri", "text_search_enterprise"),
    ("POST", "https://places.googleapis.com/v1/places:searchText", None, "text_search_enterprise"),
    ("POST", "https://places.googleapis.com/v1/places:searchText", "*", "text_search_enterprise"),
    ("GET", "https://places.googleapis.com/v1/places/abc", "internationalPhoneNumber,nationalPhoneNumber", "place_details_enterprise"),
    ("GET", "https://places.googleapis.com/v1/places/abc", "displayName", "place_details_pro"),
    ("GET", "https://places.googleapis.com/v1/places/abc", "id", None),
])
def test_quota_classify(method, url, mask, sku):
    assert quota.classify(method, url, mask) == sku


def test_quota_reserve_stops_at_cap_and_custom_caps_never_exceed_google_free_tier(sdb):
    with psycopg.connect(sdb, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('google_budget',
                     '{"text_search_pro": 3, "text_search_enterprise": 999999}'::jsonb)""")
    assert quota.caps(sdb)["text_search_pro"] == 3
    assert quota.caps(sdb)["text_search_enterprise"] == 900           # above Google's free 1,000: ignored
    assert [quota.reserve(sdb, "text_search_pro") for _ in range(3)] == [1, 2, 3]
    with pytest.raises(quota.QuotaExceeded):
        quota.reserve(sdb, "text_search_pro")
    assert quota.used(sdb)["text_search_pro"] == 3                     # the refused call was not counted
    with pytest.raises(quota.QuotaExceeded):
        quota.reserve(sdb, "text_search_enterprise", 901)
    assert "text_search_enterprise" not in quota.used(sdb)


def test_quota_allowance_spreads_the_month(sdb):
    mid = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)                 # half of November
    assert quota.allowance(sdb, "text_search_pro", burst=0, now=mid) == 2250
    with psycopg.connect(sdb, autocommit=True) as c:
        c.execute("INSERT INTO flightbridge.google_usage (month, sku, calls) VALUES (%s, 'text_search_pro', 2240)",
                  (quota.month_start(),))
    assert quota.allowance(sdb, "text_search_pro", burst=6, now=mid) == 16
    assert quota.allowance(sdb, "text_search_pro", burst=0, now=datetime(2026, 11, 1, tzinfo=timezone.utc)) == 0


def test_quota_install_meters_real_requests_calls(sdb, monkeypatch):
    seen = []
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, *a, **k: seen.append(url) or Resp({}))
    monkeypatch.setattr(quota, "_installed", {})
    quota.install(sdb)
    requests.post("https://places.googleapis.com/v1/places:searchText", json={}, headers={"X-Goog-FieldMask": sweep.MASK})
    requests.get("https://places.googleapis.com/v1/places/x", headers={"X-Goog-FieldMask": "nationalPhoneNumber"})
    requests.post("https://places.googleapis.com/v1/places:searchText", json={}, headers={"X-Goog-FieldMask": "places.id"})
    requests.get("https://example.org/")                                          # not Google: never counted
    assert len(seen) == 4
    assert quota.used(sdb) == {"text_search_pro": 1, "place_details_enterprise": 1}
    with psycopg.connect(sdb, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('google_budget', '{"text_search_pro": 1}'::jsonb)""")
    with pytest.raises(quota.QuotaExceeded):
        requests.post("https://places.googleapis.com/v1/places:searchText", json={}, headers={"X-Goog-FieldMask": sweep.MASK})
    assert len(seen) == 4                                                         # refused before leaving the machine


# ---------------------------------------------------------------- world sweep
def test_seed_queues_every_square_in_country_order(sdb):
    out = sweep.seed(sdb)
    assert out == {"pass": 1, "countries": 2, "squares": 3}
    assert sweep.seed(sdb)["squares"] == 3                                        # idempotent
    assert q(sdb, "SELECT country, priority, cells_total FROM flightbridge.sweep_country ORDER BY priority") == [("NZ", 1, 2), ("AU", 2, 1)]
    assert [r["country"] for r in sweep._next_cells(sdb, 5)] == ["NZ", "NZ", "AU"]


def test_sweep_creates_schools_matches_known_ones_and_logs_detections(sdb):
    g = FakeGoogle({(-37, 174): [[place("p1", "Ardmore Flying School"), place("p2", "Auckland Cafe & Bar", types=["cafe"]),
                                  place("p3", "Skyhigh Aviation Academy", lat=-36.9), place("p1", "Ardmore Flying School")]],
                    (-44, 172): [[place("p4", "Canterbury Aero Club", lat=-43.5, lng=172.5)]]})
    out = sweep.run(sdb, max_calls=4, session=g)
    assert out["squares"] == 2 and out["calls"] == 2 and out["new"] == 3      # cafe skipped, duplicate id ignored
    assert all(c["body"]["textQuery"] == "flight school" and c["mask"] == sweep.MASK for c in g.calls)
    assert "websiteUri" not in sweep.MASK and "PhoneNumber" not in sweep.MASK   # Pro fields only
    names = {r[0] for r in q(sdb, "SELECT name FROM flightbridge.organisation")}
    assert names == {"Ardmore Flying School", "Skyhigh Aviation Academy", "Canterbury Aero Club"}
    assert q(sdb, "SELECT count(*) FROM flightbridge.detection")[0][0] == 3
    # a square is started only with room for 3 pages: 4 - 2 calls left = 2, so AU waits for the next cycle
    assert q(sdb, "SELECT status FROM flightbridge.sweep_cell WHERE lat0=-37")[0][0] == "done"
    # second pass over the same results: matched by Google id, nothing new
    g2 = FakeGoogle({(-34, 151): [[place("p1", "Ardmore Flying School")]]})
    out2 = sweep.run(sdb, max_calls=3, session=g2)
    assert out2["new"] == 0 and out2["schools"] == 1
    assert q(sdb, "SELECT count(*) FROM flightbridge.organisation")[0][0] == 3
    row = q(sdb, "SELECT cells_done, cells_total, schools_new, finished_at IS NOT NULL FROM flightbridge.sweep_country WHERE country='NZ'")
    assert row == [(2, 2, 3, True)]


def test_full_square_is_split_into_four(sdb):
    full = [[place(f"a{p}{i}", f"Flight School {chr(65 + p)}{chr(65 + i)} Academy") for i in range(20)] for p in range(3)] \
        + [[place("zz", "Flight School ZZ Academy")]]
    g = FakeGoogle({(-37, 174): full})
    out = sweep.run(sdb, max_calls=3, session=g)
    assert out["calls"] == 3 and out["split"] == 1 and out["new"] == 60
    kids = q(sdb, "SELECT lat0, lng0, lat1, lng1, depth FROM flightbridge.sweep_cell WHERE parent_id IS NOT NULL ORDER BY lat0, lng0")
    assert kids == [(-37, 174, -36.5, 174.5, 1), (-37, 174.5, -36.5, 175, 1), (-36.5, 174, -36, 174.5, 1), (-36.5, 174.5, -36, 175, 1)]
    assert [g.calls[i]["body"].get("pageToken") for i in range(3)] == [None, "1", "2"]
    nxt = sweep._next_cells(sdb, 1)[0]
    assert nxt["depth"] == 1                                                    # finish the split square first


def test_quota_reached_puts_the_square_back(sdb):
    class Refuse:
        def post(self, *a, **k):
            raise quota.QuotaExceeded("limit")
    with pytest.raises(quota.QuotaExceeded):
        sweep.run(sdb, max_calls=3, session=Refuse())
    assert q(sdb, "SELECT count(*) FROM flightbridge.sweep_cell WHERE status='queued'")[0][0] == 3


def test_api_error_marks_square_and_stage_reports_it(sdb):
    class Broken:
        def post(self, *a, **k):
            return Resp({"error": {"message": "API key not valid"}}, 400)
    with pytest.raises(PlacesError):
        sweep.run(sdb, max_calls=3, session=Broken())
    assert q(sdb, "SELECT status, error FROM flightbridge.sweep_cell WHERE status='error'")[0][1].startswith("Places API 400")


def test_run_spends_nothing_below_three_calls(sdb):
    g = FakeGoogle({})
    assert sweep.run(sdb, max_calls=2, session=g)["squares"] == 0 and g.calls == []


def test_resweep_after_every_square_done(sdb, monkeypatch):
    sweep.run(sdb, max_calls=9, session=FakeGoogle({}))
    assert sweep._maybe_resweep(sdb) is None                                    # done, but not old enough
    with psycopg.connect(sdb, autocommit=True) as c:
        c.execute("UPDATE flightbridge.sweep_cell SET searched_at = now() - interval '200 days'")
    assert sweep._maybe_resweep(sdb)["pass"] == 2
    assert q(sdb, "SELECT count(*) FROM flightbridge.sweep_cell WHERE pass=2 AND status='queued'")[0][0] == 3


def test_locate_known_puts_older_schools_on_the_globe(sdb):
    with psycopg.connect(sdb) as c:
        oid = c.execute("""INSERT INTO flightbridge.organisation (name, country, org_kind, status)
                           VALUES ('Basair Aviation College', 'AU', 'flying_school', 'qualified') RETURNING id""").fetchone()[0]
        c.execute("INSERT INTO flightbridge.organisation_identifier (org_id, scheme, value) VALUES (%s, 'google_place_id', 'gB')", (oid,))
        c.execute("""INSERT INTO flightbridge.organisation (name, country, org_kind, status)
                     VALUES ('Register Only School', 'GB', 'flying_school', 'qualified')""")
        c.commit()
    asked = []

    class Details:
        def get(self, url, timeout=None, headers=None):
            asked.append((url, headers["X-Goog-FieldMask"]))
            return Resp({"location": {"latitude": -33.92, "longitude": 150.99}})
    assert sweep.locate_known(sdb, 10, session=Details()) == {"located": 1, "missing": 0}
    assert asked == [("https://places.googleapis.com/v1/places/gB", "location")]
    assert quota.classify("GET", asked[0][0], asked[0][1]) == "place_details_pro"
    assert sweep.locate_known(sdb, 10, session=Details()) == {"located": 0, "missing": 0}     # done once
    assert q(sdb, "SELECT name, is_new FROM flightbridge.detection") == [("Basair Aviation College", False)]


# ---------------------------------------------------------------- "Check needed" for countries without a register
def test_person_confirms_school_in_country_without_register(sdb):
    with psycopg.connect(sdb) as c:
        oid = c.execute("""INSERT INTO flightbridge.organisation (name, country, org_kind, status)
                           VALUES ('Lanseria Flight Centre', 'ZA', 'flying_school', 'research_pending') RETURNING id""").fetchone()[0]
        c.execute("INSERT INTO flightbridge.website_page (org_id, url, http_status, robots_allowed) VALUES (%s, 'https://x.example/', 200, true)", (oid,))
        assert eligibility.evaluate_org(c, oid) == "eligible_claimed"
        with pytest.raises(ValueError):
            eligibility.confirm(c, oid, None, "ATO 0123", None)                  # must say which page was checked
        assert eligibility.confirm(c, oid, None, "ATO 0123", "https://www.caa.co.za/") == "eligible_verified"
        c.commit()
