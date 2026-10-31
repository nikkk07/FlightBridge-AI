import http.server
import socketserver
import threading

import psycopg
import pytest

from flightbridge.research import pipeline
from flightbridge.research.crawler import crawl
from flightbridge.research.extract import decode_cfemail, parse
from flightbridge.research.fetch import FetchBlocked, SafeFetcher
from flightbridge.research.qualification import WEIGHTS, score
from flightbridge.research.registers import RegisterRowError, validate_row
from flightbridge.sending.service import SendingService
from flightbridge.settings import Settings

from .conftest import FULL_PROFILE


def cf_encode(email, key=0x42):
    return f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in email)


SITE = {
    "/robots.txt": "User-agent: *\nDisallow: /private\n",
    "/": '<html><body><a href="/contact">Contact</a> <a href="/private/staff">Staff</a>'
         '<a href="https://elsewhere.example/x">ext</a><p>CPL and PPL courses for international students.</p></body></html>',
    "/contact": '<html><body><p>Admissions: <a href="mailto:admissions@HOST">email us</a></p>'
                f'<p>Partnerships <span class="__cf_email__" data-cfemail="{{CF}}">[protected]</span></p>'
                '<p>Ops: ops [at] school [dot] localtest</p><p>Logo file logo@2x.png</p>'
                '<p>Agent: someone@other-agency.example</p></body></html>',
    "/private/staff": '<html><body>secret@HOST</body></html>',
}


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            host = f"127.0.0.1:{self.server.server_address[1]}"
            body = SITE.get(self.path)
            if body is None:
                self.send_response(404); self.end_headers(); return
            body = body.replace("HOST", "school.localtest").replace("{CF}", cf_encode("partners@school.localtest"))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ssrf_guard_blocks_private_hosts():
    f = SafeFetcher("t", delay_seconds=0)
    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.1/", "file:///etc/passwd",
                "http://user:pw@example.com/"):
        with pytest.raises(FetchBlocked):
            f.get(url)


def test_crawl_respects_robots_and_extracts_without_guessing(site):
    f = SafeFetcher("FlightBridgeBot-test", delay_seconds=0, allow_private_hosts=True)
    res = crawl(site + "/", f, max_pages=10)
    urls = [p[0] for p in res.pages]
    assert site + "/contact" in urls
    blocked = [p for p in res.pages if p[0].endswith("/private/staff")]
    assert blocked and blocked[0][2] is False                       # robots disallowed => not fetched
    emails = {x.email: x for x in res.findings}
    assert "secret@school.localtest" not in emails
    assert {"admissions@school.localtest", "partners@school.localtest", "ops@school.localtest"} <= set(emails)
    assert "logo@2x.png" not in emails
    assert emails["partners@school.localtest"].via == "cfemail"
    assert emails["admissions@school.localtest"].is_role_address
    assert all(x.source_url.startswith(site) for x in res.findings)
    assert not any("elsewhere.example" in u for u in urls)          # same-site only


def test_on_site_domain_attribution():
    found, _ = parse('<p>info@school.example and agent@agency.example</p>', "https://www.school.example/contact")
    m = {f.email: f.on_site_domain for f in found}
    assert m == {"info@school.example": True, "agent@agency.example": False}


def test_refusal_statement_detected():
    found, _ = parse("<p>info@school.example - no unsolicited emails please</p>", "https://school.example/")
    assert found[0].refusal_nearby


def test_no_email_means_no_contact():
    found, _ = parse("<p>Call us on +1 555 0100</p>", "https://school.example/")
    assert found == []


def test_cfemail_roundtrip():
    assert decode_cfemail(cf_encode("a@b.example")) == "a@b.example"


@pytest.mark.parametrize("row,err", [
    ({"name": "X", "country": "GB", "authority": "UK_CAA", "classification": "FTO_DGCA", "source_url": "https://caa", "approval_ref": "1"}, "invalid_for"),
    ({"name": "X", "country": "AU", "authority": "CASA", "classification": "PART141_CASA", "source_url": "https://casa", "approval_ref": "1"}, "checked_by"),
    ({"name": "X", "country": "IN", "authority": "DGCA", "classification": "FTO_DGCA", "source_url": "http://dgca"}, "https"),
    ({"name": "X", "country": "IND", "authority": "DGCA", "classification": "FTO_DGCA", "source_url": "https://d"}, "iso2"),
    ({"name": "X", "country": "IN", "authority": "DGCA", "classification": "FTO_DGCA", "source_url": "https://d"}, "approval_ref"),
])
def test_register_row_validation(row, err):
    with pytest.raises(RegisterRowError, match=err):
        validate_row(row)


def test_dto_scored_with_risk_and_unverified_gets_zero_regulatory():
    s = score({"regulatory_status": "listed_in_register", "classification": "DTO_UK", "any_contact": True})
    assert "dto_ppl_level_only" in s["risks"]
    s2 = score({"regulatory_status": None, "any_contact": True, "has_cpl": True})
    assert s2["breakdown"]["regulatory"] == 0 and s2["recommended_action"] == "needs_human_review"
    assert score({"regulatory_status": "listed_in_register", "classification": "ATO_UK"})["recommended_action"].startswith("reject")
    assert sum(WEIGHTS.values()) == 100


def test_pipeline_end_to_end_dry_run(clean_db, rules, site):
    """register -> crawl -> qualify -> draft+approval -> (CRM approves) -> central service -> dry_run_rendered."""
    rows = [validate_row({"name": "Local Test Aero", "country": "US", "authority": "FAA",
                          "classification": "PART141_FAA", "approval_ref": "TEST141X", "source_url": "https://www.faa.gov/av-info/facility-dashboard",
                          "website": "school.localtest", "checked_by": None or _emp(clean_db)})]
    stats = pipeline.import_rows(clean_db, rows)
    assert stats == {"created": 1, "matched": 0}
    assert pipeline.import_rows(clean_db, rows) == {"created": 0, "matched": 1}          # dedupe by identifier
    with psycopg.connect(clean_db) as c:
        org = c.execute("SELECT id FROM flightbridge.organisation").fetchone()[0]
    res = crawl(site + "/", SafeFetcher("t", delay_seconds=0, allow_private_hosts=True))
    # pages were served from 127.0.0.1, so attribute them to the org's domain the way production would see them
    for i, f in enumerate(res.findings):
        res.findings[i] = f.__class__(**{**f.__dict__, "on_site_domain": f.email.endswith("@school.localtest")})
    st = pipeline.store_crawl(clean_db, org, res)
    assert st["contacts"] == 3
    q = pipeline.qualify_org(clean_db, org, {"has_cpl": True, "intl_students": True})
    assert q["recommended_action"] == "draft_outreach_for_approval", q
    task = pipeline.draft_initial(clean_db, org)
    assert task and pipeline.draft_initial(clean_db, org) is None                      # one initial draft per contact
    service = SendingService(Settings(database_url=clean_db), rules, FULL_PROFILE,
                             lambda **_: (_ for _ in ()).throw(AssertionError("no transport")))
    assert service.process_ready() == []                                              # unapproved => nothing
    with psycopg.connect(clean_db) as c:
        c.execute("""UPDATE flightbridge.approval_task a SET status='approved', decided_by=%s, decided_at=now(),
                     final_subject=d.subject, final_body=d.body_text FROM flightbridge.ai_draft d
                     WHERE a.draft_id=d.id AND a.id=%s""", (_emp(clean_db), task))
    out = service.process_ready()
    assert [o.status for o in out] == ["dry_run_rendered"]


def test_missing_kill_switch_row_counts_as_engaged(clean_db, rules):
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("DELETE FROM flightbridge.system_setting")
    from flightbridge import db
    s = SendingService(Settings(database_url=clean_db, dry_run=False, outbound_enabled=True), rules, FULL_PROFILE)
    with db.tx(clean_db) as c:
        assert s._kill_switch_engaged(c) is True


def _emp(url):
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute("SELECT id FROM public.employee LIMIT 1").fetchone()
        return str(row[0]) if row else str(c.execute(
            "INSERT INTO public.employee (name, department) VALUES ('Admin','Admin') RETURNING id").fetchone()[0])
