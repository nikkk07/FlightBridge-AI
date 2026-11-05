"""Verify worker: decide Eligible / Not Qualified wherever the evidence allows; "Check needed" only for real conflicts."""
import json

import psycopg
import pytest

from flightbridge import eligibility, verify
from flightbridge.ai import site_check
from flightbridge.research import listmatch, webfind
from flightbridge.research.fetch import Page

FAA = {"faa_part141": "2026-11-05"}
BOTH = {"faa_part141": "2026-11-05", "sevp": "2026-11-05"}


def prof(**signals):
    ev = [{"signal": k, "url": "https://x.example/", "excerpt": f"text {k}"} for k, v in signals.items() if v]
    return {"signals": signals, "evidence": ev}


def ev(country, profile=None, pages=0, **ctx):
    return eligibility.evaluate(country, [], profile, pages, ctx=ctx)


def codes(res):
    return res[0], [r["code"] for r in res[1]]


# ---------------------------------------------------------------- decisions
def test_us_faa_list_loaded_sevp_not_yet():
    assert codes(ev("US", prof(aeroplane=True), 5, lists_loaded=FAA)) == ("unqualified", ["no_credential"])
    assert codes(ev("US", prof(sevp=True), 5, lists_loaded=FAA)) == ("eligible_claimed", ["claim:sevp"])     # SEVP can't be checked yet
    d, c = codes(ev("US", prof(part141=True), 5, lists_loaded=FAA))                                         # contradicted by the FAA list
    assert d == "eligible_claimed" and c[0] == "claim_not_listed"
    assert codes(ev("US", None, 0, lists_loaded=FAA)) == ("unknown", ["not_researched"])
    assert codes(ev("US", None, 0, lists_loaded=FAA, site_exhausted=True)) == ("eligible_claimed", ["no_website"])


def test_us_both_lists_loaded_decides_without_a_website():
    d, c = ev("US", None, 0, lists_loaded=BOTH)
    assert d == "unqualified" and c[0]["code"] == "not_listed" and "FAA Part 141 list (2026-11-05)" in c[0]["text"]
    assert codes(ev("US", prof(sevp=True), 4, lists_loaded=BOTH))[1][0] == "claim_not_listed"


def test_canada_needs_the_transport_canada_list_only():
    tc = {"tc_ftu": "2026-11-05"}
    assert codes(ev("CA", None, 0, lists_loaded=tc)) == ("unqualified", ["not_listed"])
    assert codes(ev("CA", prof(dli=True), 3, lists_loaded=tc)) == ("unqualified", ["not_listed"])          # DLI without an FTU
    assert codes(ev("CA", prof(transport_canada=True), 3, lists_loaded=tc))[1][0] == "claim_not_listed"


def test_listed_school_is_still_eligible_and_person_decisions_win():
    reg = [{"classification": "PART141_FAA", "status": "listed_in_register", "authority_code": "FAA", "approval_ref": "X1"}]
    assert eligibility.evaluate("US", reg, None, 0, ctx={"lists_loaded": BOTH})[0] == "eligible_verified"


def test_ai_can_remove_a_claim_only_with_a_verdict():
    p = prof(sevp=True)
    p["ai"] = {"claims": {"sevp": {"verdict": "no", "quote": "exit 12 off I-20"}}}
    d, r = ev("US", p, 4, lists_loaded=FAA)
    assert d == "unqualified" and "AI" in r[0]["text"]
    p["ai"] = {"claims": {"sevp": {"verdict": "unclear"}}}
    assert ev("US", p, 4, lists_loaded=FAA)[0] == "eligible_claimed"


def test_lookalike_goes_to_a_person_with_the_listed_name():
    d, r = ev("US", None, 0, lists_loaded=BOTH, lookalike={"name": "CLOUD DANCER AVIATION INC", "list": "faa_part141",
                                                            "how": "same town, initials CDA"})
    assert d == "eligible_claimed" and r[0]["code"] == "possible_match" and "CLOUD DANCER" in r[0]["text"]


def test_no_rules_country_with_no_website_goes_to_a_person():
    assert codes(ev("MX", None, 0, site_exhausted=True)) == ("eligible_claimed", ["no_website"])
    assert ev("MX", None, 0)[0] == "unknown"


# ---------------------------------------------------------------- list matching
def cand(name, city="Sugar Land", ref="AB1C234D"):
    return {"id": 1, "name": name, "city": city, "ref": ref, "list": "faa_part141"}


@pytest.mark.parametrize("org,listed,texts,want", [
    ({"name": "Anson Aviation", "city": "Sugar Land", "region": "Texas", "domain": "ansonair.com"}, "ANSON AIR LLC", [], "strong"),
    ({"name": "CDA Flight Academy", "city": "DeLand", "region": "Florida", "domain": "flycda.com"}, "CLOUD DANCER AVIATION INC", [], "weak"),
    ({"name": "ADF Flight School Miami", "city": "Miami", "region": "Florida", "domain": "adfairways.net"},
     "AIRCRAFT DEVELOPMENT FLIGHT AIRWAYS CORPORATION", [], "weak"),
    ({"name": "Prime Flight", "city": "Miami", "region": "Florida", "domain": "prime.example"}, "FXE FLIGHT CENTER CORP",
     ["Prime Flight is operated by FXE Flight Center Corp."], "strong"),
    ({"name": "Prime Flight", "city": "Miami", "region": "Florida", "domain": "prime.example"}, "SOMETHING ELSE INC",
     ["FAA certificate AB1C234D"], "strong"),
    ({"name": "Sky Aviation", "city": "Miami", "region": "Florida", "domain": None}, "Sky High Flight School", [], None),
    ({"name": "Florida Aviation Academy", "city": "Miami", "region": "Florida", "domain": None}, "Florida Flyers", [], None),
])
def test_listmatch(org, listed, texts, want):
    c = cand(listed, city=org["city"])
    hit = listmatch.match(org, [c], texts)
    assert (hit[0] if hit else None) == want


# ---------------------------------------------------------------- AI reading
def test_ai_no_needs_a_verbatim_quote_and_legal_names_must_be_on_the_page(monkeypatch):
    page = ["Welcome to Red Rock Flying. Find us at exit 12 off I-20 in Abilene. © 2026 Red Rock Aviation LLC"]
    monkeypatch.setattr(site_check.llm, "chat_json", lambda *a, **k: {
        "claims": {"sevp": {"verdict": "no", "quote": "exit 12 off I-20"}}, "legal_names": ["Red Rock Aviation LLC", "Made Up Inc"]})
    r = site_check.check("Red Rock Flying", page, ["sevp"], [])
    assert r["claims"]["sevp"]["verdict"] == "no" and r["legal_names"] == ["Red Rock Aviation LLC"]
    monkeypatch.setattr(site_check.llm, "chat_json", lambda *a, **k: {"claims": {"sevp": {"verdict": "no", "quote": "invented"}}})
    assert site_check.check("Red Rock Flying", page, ["sevp"], [])["claims"]["sevp"]["verdict"] == "unclear"
    monkeypatch.setattr(site_check.llm, "chat_json", lambda *a, **k: None)
    assert site_check.check("Red Rock Flying", page, ["sevp"], []) is None


# ---------------------------------------------------------------- worker, end to end
class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        html = self.pages.get(url.rstrip("/"))
        return Page(url, 200 if html else 404, html or "", True)

    def robots_allows(self, url):
        return True


def _org(c, name, city, region, domain=None, country="US"):
    return c.execute("""INSERT INTO flightbridge.organisation (name, country, region, city, website_domain, status)
                        VALUES (%s,%s,%s,%s,%s,'discovered') RETURNING id""", (name, country, region, city, domain)).fetchone()[0]


def _listed(c, name, city, region, ref, cls="PART141_FAA", auth="FAA"):
    oid = _org(c, name, city, region)
    c.execute("""INSERT INTO flightbridge.regulatory_approval (org_id, authority_code, classification, approval_ref, status,
                   source_url, checked_at, next_check_due) VALUES (%s,%s,%s,%s,'listed_in_register','https://www.faa.gov/',now(),now())""",
              (oid, auth, cls, ref))
    return oid


def _upload(c, kind):
    c.execute("""INSERT INTO flightbridge.register_upload (kind, filename, sha256, status, result, processed_at)
                 VALUES (%s,'list.csv','x','imported','{"rows": 10}', now())""", (kind,))


@pytest.fixture
def vdb(clean_db):
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("TRUNCATE flightbridge.register_upload")
        c.execute("DELETE FROM flightbridge.system_setting WHERE key IN ('eligibility_rules','cricos_sync')")
    return clean_db


def test_verify_step_takes_schools_to_a_decision(vdb, monkeypatch):
    home = ("<html><body><h1>Lone Star Flight</h1><p>Private pilot and commercial pilot training in our Cessna fleet. "
            "We are located off I-20 near the airport.</p></body></html>")
    with psycopg.connect(vdb, autocommit=True) as c:
        _upload(c, "faa_part141")
        a = _org(c, "Lone Star Flight", "Abilene", "Texas", "lonestar.example")          # website, no credential
        b = _org(c, "Anson Aviation", "Sugar Land", "Texas", "ansonair.com")             # listed under its legal name
        _listed(c, "ANSON AIR LLC", "Sugar Land", "Texas", "AN5A123B")
        d = _org(c, "Ghost Flyers", "Austin", "Texas")                                   # no website anywhere
    out = verify.step(vdb, FakeFetcher({"https://lonestar.example": home, "https://ansonair.com": home.replace("Lone Star", "Anson")}))
    assert out["errors"] == 0
    with psycopg.connect(vdb) as c:
        got = dict(c.execute("SELECT id, eligibility FROM flightbridge.organisation").fetchall())
        why_b = c.execute("SELECT eligibility_reasons FROM flightbridge.organisation WHERE id=%s", (b,)).fetchone()[0]
        nxt = c.execute("SELECT verify_next_at IS NOT NULL, verify_claim_until FROM flightbridge.organisation WHERE id=%s",
                        (d,)).fetchone()
    assert got[a] == "unqualified"                       # "I-20" here is the interstate: no SEVP claim
    assert got[b] == "eligible_verified" and "Listed in the official register: FAA AN5A123B" in why_b[0]["text"]
    # only the free guess is available in tests (no Tavily/Google keys): tried once -> no website -> a person checks
    assert got[d] == "eligible_claimed" and nxt == (True, None)


def test_rules_change_requeues_undecided_schools_once(vdb):
    with psycopg.connect(vdb, autocommit=True) as c:
        oid = _org(c, "Plain Air", "Austin", "Texas")
        c.execute("UPDATE flightbridge.organisation SET eligibility='unqualified', eligibility_checked_at=now() WHERE id=%s", (oid,))
    assert eligibility.run(vdb, 50)["rules_changed"] is True
    assert eligibility.run(vdb, 50)["rules_changed"] is False
    with psycopg.connect(vdb, autocommit=True) as c:
        _upload(c, "sevp")
        _upload(c, "faa_part141")
    r = eligibility.run(vdb, 50)
    assert r["rules_changed"] is True and r["unqualified"] == 1        # both US lists loaded: absent -> Not Qualified
    with psycopg.connect(vdb) as c:
        why = c.execute("SELECT eligibility_reasons FROM flightbridge.organisation WHERE id=%s", (oid,)).fetchone()[0]
    assert why[0]["code"] == "not_listed"


def test_a_person_decision_is_never_requeued(vdb):
    with psycopg.connect(vdb, autocommit=True) as c:
        oid = _org(c, "Human Says No", "Austin", "Texas")
        c.execute("""UPDATE flightbridge.organisation SET eligibility='unqualified',
                     eligibility_reasons='[{"code": "human_unqualified", "text": "x"}]' WHERE id=%s""", (oid,))
    verify.step(vdb, FakeFetcher({}))
    with psycopg.connect(vdb) as c:
        assert c.execute("SELECT eligibility, eligibility_reasons->0->>'code' FROM flightbridge.organisation").fetchone() == \
            ("unqualified", "human_unqualified")


def test_sevp_upload_keeps_aviation_schools_only():
    from flightbridge.research import list_upload
    csv = ("School Name,Campus Name,City,State,Visa Type\nSheble Aviation,Main,Ardmore,OK,M\n"
           "Springfield High School,Main,Springfield,IL,F\nPhoenix East Aviation,Main,Daytona Beach,FL,M\n").encode()
    rows, rep = list_upload.parse("sevp", csv, "sevp.csv", None, "2026-11-05")
    assert [r.name for r in rows] == ["Sheble Aviation", "Phoenix East Aviation"]
    assert rows[0].classification == "SEVP_M1" and rows[0].region == "Oklahoma" and rep["skipped"]["not_matching"] == 1


def test_template_placeholders_state_and_credential():
    from flightbridge.outreach.templates import org_values, render, validate
    v = validate("Your {{credential}} in {{state}}", "Dear {{school_name}} team, we noticed your {{credential}} in {{city}}.")
    assert v["ok"] and v["placeholders"] == ["city", "credential", "school_name", "state"]
    vals = org_values("Sheble Aviation", "Ardmore", "US", "Oklahoma", ["SEVP_M1", "PART141_FAA"])
    assert render("{{credential}}", "x {{state}} {{country}} " * 3, vals)[0] == "FAA Part 141 certification"
    assert org_values("X", None, "CA")["state"] == "Canada" and org_values("X", None, "CA")["credential"] == "pilot training programme"
