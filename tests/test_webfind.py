"""Website finding without Google (guess + Tavily), the FAA Part 141 upload, and the re-check stage."""
import base64
import importlib

import pytest

from flightbridge.research.fetch import Page

webfind = importlib.import_module("flightbridge.research.webfind")

SITE = "<html><body><h1>Sheble Aviation</h1><p>Private pilot and commercial pilot training at Joplin, Missouri. Cessna 172 fleet.</p></body></html>"
PARKED = "<html><body>shebleaviation.com - This domain may be for sale! Buy this domain today.</body></html>"
OTHER = "<html><body><h1>Sheble Plumbing</h1><p>Pipes and boilers in Joplin.</p></body></html>"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        html = self.pages.get(url.rstrip("/"))
        return Page(url, 200 if html else 404, html or "", True)

    def robots_allows(self, url):
        return True


def real_guess():
    # conftest replaces webfind.guess for offline worker tests; reload the module to test the real function
    return importlib.reload(webfind).guess


def test_labels_and_candidates():
    assert webfind.labels("Sheble Aviation LLC")[:2] == ["shebleaviation", "sheble-aviation"]
    assert "sheble" in webfind.labels("Sheble Aviation LLC")
    c = webfind.candidates("Sheble Aviation", "US")
    assert c[0] == "shebleaviation.com" and "shebleaviation.aero" in c
    assert webfind.candidates("Basair Aviation College", "AU")[0] == "basairaviationcollege.com.au"
    assert webfind.labels("The LLC Inc") == []


def test_guess_accepts_only_a_real_flying_school_page():
    guess = real_guess()
    live = {"shebleaviation.com", "sheble.com"}
    f = FakeFetcher({"https://shebleaviation.com": PARKED, "https://sheble.com": SITE})
    res = guess("Sheble Aviation", "Joplin", "Missouri", "US", None, f, resolver=lambda d: d in live)
    assert res["status"] == "found" and res["domain"] == "sheble.com" and res["method"] == "guess"
    assert "joplin" in [p.lower() for p in res["place_on_site"]]
    # a different business with the same word, and a parked domain, are both refused
    f2 = FakeFetcher({"https://shebleaviation.com": PARKED, "https://sheble.com": OTHER})
    assert guess("Sheble Aviation", "Joplin", "Missouri", "US", None, f2, resolver=lambda d: d in live)["status"] == "not_found"
    # nothing resolves: no page is fetched at all
    f3 = FakeFetcher({})
    assert guess("Sheble Aviation", None, None, "US", None, f3, resolver=lambda d: False)["status"] == "not_found"
    assert f3.calls == []


class Resp:
    def __init__(self, status, data):
        self.status_code, self._d, self.text = status, data, str(data)

    def json(self):
        return self._d


class Session:
    def __init__(self, resp):
        self.resp, self.sent = resp, []

    def post(self, url, **kw):
        self.sent.append((url, kw))
        return self.resp


def test_tavily_skips_directories_and_confirms_on_the_site():
    sess = Session(Resp(200, {"results": [
        {"url": "https://www.facebook.com/shebleaviation", "title": "Sheble Aviation | Facebook"},
        {"url": "https://www.yelp.com/biz/sheble-aviation", "title": "Sheble Aviation - Yelp"},
        {"url": "https://www.sheble.com/flight-training", "title": "Flight training"}]}))
    f = FakeFetcher({"https://www.sheble.com": SITE})
    res = webfind.tavily("Sheble Aviation", "Joplin", "Missouri", "US", None, f, "tvly-test", session=sess)
    assert res["status"] == "found" and res["domain"] == "sheble.com" and res["method"] == "tavily"
    url, kw = sess.sent[0]
    assert url == "https://api.tavily.com/search" and kw["headers"]["Authorization"] == "Bearer tvly-test"
    assert kw["json"]["search_depth"] == "basic" and "Joplin" in kw["json"]["query"]
    assert not any("facebook" in c or "yelp" in c for c in f.calls)
    with pytest.raises(RuntimeError):
        webfind.tavily("X", None, None, "US", None, f, "bad", session=Session(Resp(401, {"detail": "bad key"})))


def _org(conn, name, region="Missouri", country="US"):
    return conn.execute("""INSERT INTO flightbridge.organisation (name, country, region, city, status)
                           VALUES (%s,%s,%s,'Joplin','research_pending') RETURNING id""", (name, country, region)).fetchone()[0]


def test_find_websites_cheapest_first_and_paced(clean_db, monkeypatch):
    import psycopg
    from flightbridge import automation
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("DELETE FROM flightbridge.google_usage")
        a = _org(c, "Sheble Aviation")
        b = _org(c, "Joplin Flight Center")
    found = {"status": "found", "url": "https://sheble.com/", "domain": "sheble.com", "matched_tokens": ["sheble"],
             "approval_ref_on_site": False}
    monkeypatch.setattr(webfind, "guess", lambda name, *a, **k: {**found, "method": "guess"} if "Sheble" in name
                        else {"status": "not_found", "method": "guess", "tried": []})
    tav = []
    monkeypatch.setattr(webfind, "tavily", lambda name, *a, **k: tav.append(name) or {
        "status": "found", "url": "https://joplinflight.com/", "domain": "joplinflight.com", "matched_tokens": ["joplin"],
        "method": "tavily", "approval_ref_on_site": False})
    google = []
    monkeypatch.setattr(automation, "find_website", lambda *a, **k: google.append(a) or {"status": "not_found"})
    out = automation.find_websites(clean_db, FakeFetcher({}), limit=1, tavily_limit=1)
    assert out["found"] == 2 and out["found_by"] == {"guess": 1, "tavily": 1, "google": 0}
    assert tav == ["Joplin Flight Center"] and google == []          # Tavily only after a failed guess; Google not needed
    with psycopg.connect(clean_db, autocommit=True) as c:
        doms = dict(c.execute("SELECT id, website_domain FROM flightbridge.organisation").fetchall())
        acts = sorted(r[0] for r in c.execute("SELECT action FROM flightbridge.audit_log WHERE action LIKE 'website_%'"))
        used = c.execute("SELECT calls FROM flightbridge.google_usage WHERE sku='tavily_search'").fetchone()[0]
    assert doms[a] == "sheble.com" and doms[b] == "joplinflight.com"
    assert acts == ["website_guess", "website_guess", "website_tavily"] and used == 1


def test_find_websites_respects_retry_and_zero_allowance(clean_db, monkeypatch):
    import psycopg
    from flightbridge import automation
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with psycopg.connect(clean_db, autocommit=True) as c:
        _org(c, "Nowhere Aero Club")
    calls = []
    monkeypatch.setattr(webfind, "guess", lambda *a, **k: calls.append("guess") or {"status": "not_found", "method": "guess"})
    monkeypatch.setattr(automation, "find_website", lambda *a, **k: calls.append("google") or {"status": "not_found"})
    automation.find_websites(clean_db, FakeFetcher({}), limit=0)      # no Google allowance, no Tavily key
    assert calls == ["guess"]
    automation.find_websites(clean_db, FakeFetcher({}), limit=0)      # guessed already this month: nothing to do
    assert calls == ["guess"]
    automation.find_websites(clean_db, FakeFetcher({}), limit=1)      # Google allowance appears: only Google runs
    assert calls == ["guess", "google"]


FAA_CSV = ("Facility Name\tFacility Type\tCity\tState\tCertificate Number\n"
           "SHEBLE AVIATION LLC\tPart 141 Pilot School\tJOPLIN\tMO\tSA2S123K\n"
           "ACME HELICOPTER REPAIR\tRepair Station\tTULSA\tOK\tR9X\n"
           "NEW HORIZONS FLIGHT ACADEMY\tPart 141 Pilot School\tDENVER\tCO\tNH4S555Q\n"
           "NEW HORIZONS FLIGHT ACADEMY\tPart 141 Pilot School\tDENVER\tCO\tNH4S555Q\n")


def test_faa_upload_parse_utf16_tab_and_type_filter():
    from flightbridge.research import faa_upload
    rows, rep = faa_upload.parse(FAA_CSV.encode("utf-16"), "faa.csv", None, "2026-11-05")
    assert [r.name for r in rows] == ["SHEBLE AVIATION LLC", "NEW HORIZONS FLIGHT ACADEMY"]
    assert rows[0].region == "Missouri" and rows[0].city == "Joplin" and rows[0].approval_ref == "SA2S123K"
    assert rows[0].approval_status == "listed_in_register" and rows[0].classification == "PART141_FAA"
    assert rep["skipped"] == {"not_part141": 1, "no_name": 0, "duplicate": 1}
    assert rep["columns"]["certificate"] == "Certificate Number"
    rows2, _ = faa_upload.parse(FAA_CSV.replace("\t", ",").encode(), "faa.csv", None, "2026-11-05")
    assert len(rows2) == 2
    with pytest.raises(faa_upload.UploadError):
        faa_upload.parse(b"a,b\n1,2\n", "x.csv", None, "2026-11-05")


def test_faa_upload_parse_xlsx():
    import io
    from openpyxl import Workbook
    from flightbridge.research import faa_upload
    wb = Workbook()
    ws = wb.active
    ws.append(["FAA Part 141 Pilot Schools"])
    ws.append(["School Name", "City", "State", "Designator"])
    ws.append(["Sheble Aviation", "Joplin", "MO", "SA2S123K"])
    buf = io.BytesIO()
    wb.save(buf)
    rows, rep = faa_upload.parse(buf.getvalue(), "faa.xlsx", None, "2026-11-05")
    assert len(rows) == 1 and rows[0].approval_ref == "SA2S123K" and rep["columns"]["type"] is None


def test_upload_import_matches_by_name_and_makes_eligible(clean_db):
    import psycopg
    from flightbridge import automation, eligibility
    with psycopg.connect(clean_db, autocommit=True) as c:
        emp = c.execute("INSERT INTO public.employee (name) VALUES ('Admin') RETURNING id").fetchone()[0]
        sheble = _org(c, "Sheble Aviation")
        other_state = _org(c, "New Horizons Flight Academy", region="Florida")     # same name, different state
        c.execute("""INSERT INTO flightbridge.register_upload (kind, filename, content_b64, sha256, uploaded_by)
                     VALUES ('faa_part141','faa.csv',%s,'x',%s)""", (base64.b64encode(FAA_CSV.encode()).decode(), emp))
    out = automation.import_uploads(clean_db)
    assert out == {"files": 1, "rows": 2, "created": 1, "matched": 1, "errors": 0}
    eligibility.run(clean_db, 50)
    with psycopg.connect(clean_db, autocommit=True) as c:
        st = dict(c.execute("SELECT id, eligibility FROM flightbridge.organisation").fetchall())
        up = c.execute("SELECT status, content_b64, result->>'created' FROM flightbridge.register_upload").fetchone()
        n = c.execute("SELECT count(*) FROM flightbridge.organisation WHERE name ILIKE 'new horizons%'").fetchone()[0]
    assert st[sheble] == "eligible_verified"
    assert st[other_state] != "eligible_verified" and n == 2            # Colorado school added, Florida one untouched
    assert up == ("imported", None, "1")


def test_upload_bad_file_is_reported_not_fatal(clean_db):
    import psycopg
    from flightbridge import automation
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.register_upload (kind, filename, content_b64, sha256)
                     VALUES ('faa_part141','x.csv',%s,'x')""", (base64.b64encode(b"a,b\n1,2\n").decode(),))
    assert automation.import_uploads(clean_db)["errors"] == 1
    with psycopg.connect(clean_db, autocommit=True) as c:
        st, msg = c.execute("SELECT status, result->>'error' FROM flightbridge.register_upload").fetchone()
    assert st == "error" and "name" in msg


def test_recheck_rereads_old_profiles_once_a_week(clean_db, monkeypatch):
    import psycopg
    from flightbridge import automation
    with psycopg.connect(clean_db, autocommit=True) as c:
        oid = c.execute("""INSERT INTO flightbridge.organisation (name, country, website_domain, status)
                           VALUES ('Old School','US','old.example','research_completed') RETURNING id""").fetchone()[0]
        c.execute("""INSERT INTO flightbridge.organisation_profile (org_id, summary, signals, evidence, generator, updated_at)
                     VALUES (%s,'', '{}', '[]', 'test', now() - interval '120 days')""", (oid,))
    seen = []
    monkeypatch.setattr(automation.pipeline, "research_org", lambda url, o, f, **k: seen.append(o) or {})
    assert automation.recheck(clean_db, FakeFetcher({}), 5) == {"rechecked": 1}
    assert automation.recheck(clean_db, FakeFetcher({}), 5) == {"rechecked": 0}       # site down: retried next week
    assert seen == [str(oid)]
