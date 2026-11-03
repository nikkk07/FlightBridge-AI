"""Contact finding: ROT13 / spoken / JSON-LD emails, free-mail the school publishes, social links, enquiry forms,
contact pages first, a rebranded domain, and the contact-hunt stage."""
import http.server
import socketserver
import threading

import psycopg
import pytest

from flightbridge.research import pipeline
from flightbridge.research.crawler import crawl
from flightbridge.research.extract import parse_page
from flightbridge.research.fetch import SafeFetcher


def test_hidden_and_spoken_emails_only_on_the_schools_domain():
    html = ('<p>Write to syl[at]inagntrnivngvba[dot]pbz or ops at vantageaviation dot com, '
            'or sales at another-firm dot com.</p>'
            '<script type="application/ld+json">{"@type":"Organization","email":"mailto:info@vantageaviation.com"}</script>')
    found, _, _ = parse_page(html, "https://www.vantageaviation.com/contact")
    on_site = {f.email for f in found if f.on_site_domain}
    assert on_site == {"fly@vantageaviation.com", "ops@vantageaviation.com", "info@vantageaviation.com"}
    assert not any("another-firm" in f.email for f in found)


def test_freemail_kept_but_web_designer_credit_is_not():
    html = ('<p>Call or email the chief instructor at skyhawk.school@gmail.com any time. '
            'Some more words so the credit line is far away from the address above.</p>'
            '<footer>Website by pixels.studio@gmail.com</footer>')
    found = {f.email: f for f in parse_page(html, "https://skyhawkflight.com/contact")[0]}
    assert found["skyhawk.school@gmail.com"].freemail is True
    assert found["pixels.studio@gmail.com"].freemail is False


def test_social_links_and_enquiry_form():
    html = ('<a href="https://www.linkedin.com/company/sky-academy/">LinkedIn</a>'
            '<a href="https://www.facebook.com/sharer/sharer.php?u=x">share</a>'
            '<a href="https://www.facebook.com/skyacademy">Facebook</a>'
            '<form action="/send"><input type="email" name="e"><textarea></textarea></form>')
    _, _, extras = parse_page(html, "https://skyacademy.com/contact")
    assert extras.contact_form is True
    assert extras.socials == [("linkedin", "https://www.linkedin.com/company/sky-academy"),
                              ("facebook", "https://www.facebook.com/skyacademy")]


PAGES = {
    "/": '<html><body><a href="/news/1">news</a><a href="/news/2">news</a><a href="/news/3">news</a></body></html>',
    "/news/1": "<p>news</p>", "/news/2": "<p>news</p>", "/news/3": "<p>news</p>",
    "/privacy-policy": "<p>Questions about your data: privacy@school.localtest</p>",
    "/contact-us": '<p>Enquiries</p><form><textarea></textarea></form>'
                   '<a href="https://www.linkedin.com/school/local-test-aero/">in</a>',
}


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/robots.txt":
                body, ctype = "User-agent: *\nAllow: /\n", "text/plain"
            elif self.path in PAGES:
                body, ctype = PAGES[self.path], "text/html"
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", ctype); self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_contact_pages_are_read_first_and_missing_guesses_are_not_recorded(site):
    res = crawl(site + "/", SafeFetcher("t", delay_seconds=0, allow_private_hosts=True), max_pages=3, seed_contact_paths=True)
    read = [u for u, *_ in res.pages]
    assert read[0].endswith("/") and {u.rsplit("/", 1)[1] for u in read[1:]} == {"contact-us", "privacy-policy"}
    assert not any(st == 404 for _, st, _, _ in res.pages)
    assert [f.email for f in res.findings] == ["privacy@school.localtest"]
    assert res.contact_forms == [site + "/contact-us"]
    assert res.socials == [("linkedin", "https://www.linkedin.com/school/local-test-aero", site + "/contact-us")]


def test_store_crawl_keeps_freemail_and_channels(clean_db, site):
    with psycopg.connect(clean_db, autocommit=True) as c:
        oid = c.execute("""INSERT INTO flightbridge.organisation (name, country, website_domain, status)
                           VALUES ('Local Test Aero','US','school.localtest','research_completed') RETURNING id""").fetchone()[0]
    res = crawl(site + "/", SafeFetcher("t", delay_seconds=0, allow_private_hosts=True), max_pages=3, seed_contact_paths=True)
    res.findings.extend(parse_page("<p>Email us: localtestaero@gmail.com</p>", site + "/contact-us")[0])
    st = pipeline.store_crawl(clean_db, str(oid), res)
    assert st["channels"] == 2
    with psycopg.connect(clean_db) as c:
        emails = {r[0] for r in c.execute("SELECT email FROM flightbridge.contact WHERE org_id=%s", (oid,))}
        kinds = {r[0] for r in c.execute("SELECT kind FROM flightbridge.contact_channel WHERE org_id=%s", (oid,))}
    assert emails == {"localtestaero@gmail.com"}            # privacy@ is on 127.0.0.1 here, so not attributed
    assert kinds == {"contact_form", "linkedin"}
