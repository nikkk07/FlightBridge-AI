"""Autopilot: website discovery (confirmed on the school's own site), research, qualify, draft, Run-now queue, run log."""
import pytest

from flightbridge import automation
from flightbridge.outreach.templates import approve_template, save_template
from flightbridge.research import places, signals
from flightbridge.research.fetch import Page
from flightbridge.research.pipeline import import_rows
from flightbridge.research.regions import uk_nation
from flightbridge.research.registers import validate_row
from flightbridge.settings import Settings
from flightbridge.worker import run_once

HOME = """<html><title>Aeros Flight Training</title><body>Aeros Flight Training — UK CAA ATO GBR.ATO.0105.
CPL and PPL courses. We welcome international students and offer visa letters. Our fleet: Piper PA-28 Warrior.
<a href="/contact">Contact</a></body></html>"""
CONTACT = "<html><body>Email us at <a href='mailto:info@aeros.example'>info@aeros.example</a></body></html>"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        html = self.pages.get(url.rstrip("/"))
        return Page(url, 200 if html else 404, html or "", True)

    def robots_allows(self, url):
        return True


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._d, self.text = status, data, str(data)

    def json(self):
        return self._d


class FakeSession:
    def __init__(self, resp):
        self.resp, self.sent = resp, []

    def post(self, url, **kw):
        self.sent.append((url, kw))
        return self.resp


def _row(**kw):
    base = {"name": "Aeros Limited T/A Aeros Flight Training", "country": "GB", "authority": "UK_CAA",
            "classification": "ATO_UK", "approval_ref": "GBR.ATO.0105", "source_url": "https://www.caa.co.uk/publication/download/13488",
            "city": "Warwickshire", "region": "England",
            "courses": "Commercial Pilot Licence (Aeroplanes) Modular | Private Pilot Licence (Aeroplanes)"}
    base.update(kw)
    return validate_row(base)


@pytest.mark.parametrize("addr,nation", [
    (["Hawarden", "Flint Road", "Chester", "CH4 0GZ"], "Wales"), (["Chester", "CH4 7AB"], "England"),
    (["Perth Airport", "PH2 6PL"], "Scotland"), (["Belfast", "BT29 4AB"], "Northern Ireland"),
    (["Sleap Airfield", "Shrewsbury", "SY4 3HE"], "England"), (["Aberystwyth", "SY23 1AA"], "Wales"), (["no postcode"], None)])
def test_uk_nation_from_postcode(addr, nation):
    assert uk_nation(addr) == nation


def test_signals_quote_the_text_that_triggered_them():
    out = signals.extract([("https://aeros.example/", signals.visible_text(HOME))])
    assert out["signals"]["intl_students"] and out["signals"]["fleet_published"] and out["signals"]["has_cpl"]
    ev = {e["signal"]: e for e in out["evidence"]}
    assert "international students" in ev["intl_students"]["excerpt"] and ev["intl_students"]["url"] == "https://aeros.example/"


def test_places_candidate_is_kept_only_if_the_school_site_confirms_it():
    sess = FakeSession(FakeResp(200, {"places": [
        {"id": "p-wrong", "displayName": {"text": "Aeros Cafe"}, "websiteUri": "https://cafe.example/"},
        {"id": "p-1", "displayName": {"text": "Aeros Flight Training"}, "websiteUri": "https://www.aeros.example/"}]}))
    f = FakeFetcher({"https://www.aeros.example": HOME, "https://cafe.example": "<p>Coffee</p>"})
    res = places.find_website("Aeros Limited T/A Aeros Flight Training", "Warwickshire", "GB", "GBR.ATO.0105", f, "k", sess)
    assert res["status"] == "found" and res["domain"] == "aeros.example" and res["place_id"] == "p-1" and res["approval_ref_on_site"]
    assert sess.sent[0][1]["headers"]["X-Goog-FieldMask"] == places.FIELD_MASK
    # same candidate but the homepage does not name the school -> rejected
    f2 = FakeFetcher({"https://www.aeros.example": "<p>Domain for sale</p>"})
    assert places.find_website("Aeros Limited T/A Aeros Flight Training", None, "GB", None, f2, "k", sess)["status"] == "not_found"


def test_places_api_error_is_reported_not_swallowed():
    sess = FakeSession(FakeResp(403, {"error": {"message": "Places API (New) has not been used in project 1"}}))
    with pytest.raises(places.PlacesError, match="403"):
        places.search("X", None, "GB", "k", sess)


def test_full_autopilot_pipeline_ends_in_approval_queue(clean_db, monkeypatch):
    import_rows(clean_db, [_row()])
    monkeypatch.setattr(automation, "find_website", lambda *a, **k: {
        "status": "found", "place_id": "p-1", "url": "https://aeros.example/", "domain": "aeros.example"})
    f = FakeFetcher({"https://aeros.example": HOME, "https://aeros.example/contact": CONTACT})
    import psycopg
    with psycopg.connect(clean_db, autocommit=True) as c:
        emp = str(c.execute("INSERT INTO public.employee (name) VALUES ('Owner') RETURNING id").fetchone()[0])
    t = save_template(clean_db, "initial", "Partnership enquiry for {{school_name}}",
                      "Dear {{school_name}} team,\n\nWe are a DGCA ground school in Delhi and would like to talk.\n\nRegards", emp)
    approve_template(clean_db, t["id"], emp)

    out = run_once(Settings(database_url=clean_db), reader=None, fetcher=f)
    r = out["research"]
    assert r["find_websites"]["found"] == 1 and r["research"]["emails_found"] == 1
    assert r["qualify"]["qualified"] == 1 and r["draft"]["drafts_created"] == 1
    with psycopg.connect(clean_db) as c:
        org = c.execute("SELECT status, website_domain, region FROM flightbridge.organisation").fetchone()
        prof = c.execute("SELECT signals->>'intl_students', generator FROM flightbridge.organisation_profile").fetchone()
        task = c.execute("SELECT status FROM flightbridge.approval_task").fetchone()
        run = c.execute("SELECT status, runner FROM flightbridge.worker_run").fetchone()
        sent = c.execute("SELECT count(*) FROM flightbridge.outbound_message").fetchone()[0]
    assert org == ("qualified", "aeros.example", "England")
    assert prof == ("true", "keywords-v1")
    assert task == ("open",) and sent == 0          # waits for a human; nothing queued to send
    assert run[0] == "ok"


def test_no_email_goes_to_a_person_not_rejected(clean_db):
    import_rows(clean_db, [_row(website="https://aeros.example")])
    f = FakeFetcher({"https://aeros.example": HOME})
    out = run_once(Settings(database_url=clean_db), reader=None, fetcher=f)
    assert out["research"]["qualify"]["needs_review"] == 1
    import psycopg
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT status FROM flightbridge.organisation").fetchone()[0] == "needs_human_review"


def test_places_failure_is_partial_and_other_stages_still_run(clean_db, monkeypatch):
    import_rows(clean_db, [_row(), _row(name="Other School Ltd", approval_ref="GBR.ATO.0999", website="https://other.example")])

    def boom(*a, **k):
        raise places.PlacesError("Places API 403: not enabled")
    monkeypatch.setattr(automation, "find_website", boom)
    f = FakeFetcher({"https://other.example": HOME.replace("Aeros", "Other"), "https://other.example/contact": CONTACT})
    out = run_once(Settings(database_url=clean_db), reader=None, fetcher=f)
    assert "403" in out["research"]["find_websites_error"] and out["research"]["research"]["researched"] == 1
    import psycopg
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT status FROM flightbridge.worker_run").fetchone()[0] == "partial"


def test_run_now_queue_research_one_school(clean_db):
    import_rows(clean_db, [_row(website="https://aeros.example")])
    import psycopg
    with psycopg.connect(clean_db, autocommit=True) as c:
        org = c.execute("SELECT id FROM flightbridge.organisation").fetchone()[0]
        c.execute("INSERT INTO flightbridge.job_request (kind, org_id) VALUES ('research_school', %s)", (org,))
        with pytest.raises(psycopg.errors.UniqueViolation):     # a second click doesn't queue duplicate work
            c.execute("INSERT INTO flightbridge.job_request (kind, org_id) VALUES ('research_school', %s)", (org,))
    f = FakeFetcher({"https://aeros.example": HOME, "https://aeros.example/contact": CONTACT})
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("UPDATE flightbridge.system_setting SET value='{\"find_websites\": false, \"research\": false, \"qualify\": false, \"draft\": false, \"discover_areas\": false, \"find_contacts\": false}' WHERE key='autopilot'")
    out = run_once(Settings(database_url=clean_db), reader=None, fetcher=f, trigger="queue")
    assert out["jobs"][0]["research"]["emails_found"] == 1 and set(out["research"]) <= {"registers", "cricos", "assign_areas", "eligibility"}   # research stages off
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT status FROM flightbridge.job_request").fetchone()[0] == "done"
        assert c.execute("SELECT trigger FROM flightbridge.worker_run").fetchone()[0] == "queue"


def test_register_sync_imports_once_per_file_version(clean_db, tmp_path):
    import csv, shutil
    from flightbridge.automation import REGISTER_DIR as _unused  # noqa: F401 (patched by conftest)
    src = next(__import__("pathlib").Path(__file__).resolve().parents[1].glob("flightbridge/registers/uk_caa/*_register.csv"))
    d = tmp_path / "regs" / "uk_caa"; d.mkdir(parents=True)
    shutil.copy(src, d / src.name)
    n = sum(1 for _ in csv.DictReader(open(src)))
    first = automation.sync_registers(clean_db, tmp_path / "regs")
    assert first["files_imported"] == 1 and first["created"] == n and first["rejected_rows"] == 0
    import psycopg
    with psycopg.connect(clean_db) as c:
        checked = c.execute("SELECT max(checked_at) FROM flightbridge.regulatory_approval").fetchone()[0]
        regions = dict(c.execute("SELECT region, count(*) FROM flightbridge.organisation GROUP BY 1").fetchall())
    assert regions.get("Scotland") == 1 and regions.get("Wales") == 1
    # unchanged file -> nothing re-imported, regulator check dates untouched
    assert automation.sync_registers(clean_db, tmp_path / "regs")["files_imported"] == 0
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT max(checked_at) FROM flightbridge.regulatory_approval").fetchone()[0] == checked


def test_contact_hunt_reads_privacy_page_once_and_then_waits(clean_db):
    import psycopg
    with psycopg.connect(clean_db, autocommit=True) as c:
        oid = c.execute("""INSERT INTO flightbridge.organisation (name, country, website_domain, status)
                           VALUES ('Aeros','GB','aeros.example','research_completed') RETURNING id""").fetchone()[0]
    f = FakeFetcher({"https://aeros.example": "<p>Home</p>",
                     "https://aeros.example/privacy-policy": "<p>Data questions: dpo [at] aeros [dot] example</p>"
                                                             "<a href='https://www.linkedin.com/company/aeros-ft/'>in</a>"})
    out = automation.find_contacts(clean_db, f, 5)
    assert out["searched"] == 1 and out["emails_found"] == 1 and out["channels_found"] == 1
    assert automation.find_contacts(clean_db, f, 5)["searched"] == 0      # has an email now
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("DELETE FROM flightbridge.contact_fact_evidence"); c.execute("DELETE FROM flightbridge.contact")
    assert automation.find_contacts(clean_db, f, 5)["searched"] == 0      # retried only after 14 days
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT email FROM flightbridge.contact").fetchall() == []
        assert c.execute("SELECT count(*) FROM flightbridge.audit_log WHERE action='contact_hunt' AND entity_id=%s",
                         (str(oid),)).fetchone()[0] == 1
