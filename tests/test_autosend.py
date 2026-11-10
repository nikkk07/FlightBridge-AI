"""Email Auto-Pilot (flightbridge/autosend.py) and its pre-flight checks (flightbridge/deliverability.py)."""
import json
import random
from datetime import date, datetime, timezone

import psycopg
import pytest

from flightbridge import autosend, deliverability
from flightbridge.compliance.suppression import email_domain, email_hash
from flightbridge.localtime import business_time, in_business_hours, school_tz
from flightbridge.outreach.templates import approve_template, save_template
from flightbridge.sending.service import SendingService
from flightbridge.sending.transports import SendRequest, SmtpImapTransport
from flightbridge.settings import Settings

from .conftest import FULL_PROFILE

WED_10AM_TEXAS = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)      # 10:00 in Chicago, a Wednesday
WED_MIDNIGHT_TEXAS = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


class FakeDNS:
    def __init__(self, dmarc=("v=DMARC1; p=none",), spf="v=spf1 +a +mx ip4:203.0.113.5 ~all", dkim=True, ptr=True):
        self.records = {"weoneaviation.in": [spf] if spf else [], "_dmarc.weoneaviation.in": list(dmarc)}
        if dkim:
            self.records["default._domainkey.weoneaviation.in"] = ["v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEB"]
        self.ptr_ok = ptr

    def txt(self, name):
        return self.records.get(name, [])

    def a(self, name):
        return {"host2.dnsinweb.com": ["203.0.113.5"], "mail.example.net": ["203.0.113.5"]}.get(name, [])

    def aaaa(self, name):
        return []

    def mx(self, name):
        return ["host2.dnsinweb.com"] if name == "weoneaviation.in" else []

    def ptr(self, ip):
        return ["mail.example.net"] if self.ptr_ok else []

    @staticmethod
    def blocklist(qname):
        return "clean"


class FakeTransport:
    calls = []
    behaviour = "ok"

    def __init__(self, capability):
        pass

    def send(self, req):
        FakeTransport.calls.append(req)
        if FakeTransport.behaviour == "reject":
            from flightbridge.sending.transports import DefiniteRejection
            raise DefiniteRejection("smtp_550")
        return f"<msg{len(FakeTransport.calls)}@weoneaviation.in>"


@pytest.fixture
def ap(clean_db):
    FakeTransport.calls, FakeTransport.behaviour = [], "ok"
    with psycopg.connect(clean_db, autocommit=True) as c:
        emp = c.execute("INSERT INTO public.employee (name, department) VALUES ('Owner','Admin') RETURNING id").fetchone()[0]
        c.execute("UPDATE flightbridge.system_setting SET value='false'::jsonb WHERE key='outbound_kill_switch'")
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('send_autopilot', %s::jsonb),
                     ('send_autopilot_state', '{}'::jsonb), ('integrations', %s::jsonb)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                  (json.dumps({"enabled": True, "by": str(emp), "daily_limit": 40, "warmup": True,
                               "since": "2026-11-01T00:00:00+00:00"}),
                   json.dumps({"send_from": "hiox", "hiox_mail_user": "partnership@weoneaviation.in",
                               "hiox_mail_host": "host2.dnsinweb.com"})))
        c.execute("""INSERT INTO flightbridge.integration_secret (name, ciphertext, last4, last_test_ok, last_test_message)
                     VALUES ('hiox_mail_password', 'x', 'abcd', true, 'Login works') ON CONFLICT (name) DO UPDATE
                     SET last_test_ok = true""")
    t = save_template(clean_db, "initial", "Pilot training partnership for {{school_name}}",
                      "Dear {{school_name}} team,\n\nWe train student pilots in Delhi and would like to talk.", str(emp))
    approve_template(clean_db, t["id"], str(emp))
    from flightbridge import secrets
    secrets._cache.clear()                    # settings are cached for 60 s in the engine
    return {"url": clean_db, "emp": str(emp)}


def school(url, name="Lone Star Aviation", email="info@lonestar.example", region="TX", country="US"):
    with psycopg.connect(url, autocommit=True) as c:
        org = c.execute("""INSERT INTO flightbridge.organisation (name, country, region, status, eligibility)
                           VALUES (%s,%s,%s,'qualified','eligible_verified') RETURNING id""", (name, country, region)).fetchone()[0]
        c.execute("""INSERT INTO flightbridge.contact (org_id,email,email_hash,email_domain,is_role_address,source_url)
                     VALUES (%s,%s,%s,%s,true,'https://lonestar.example/contact')""",
                  (org, email, email_hash(email), email_domain(email)))
    from flightbridge.research import pipeline
    assert pipeline.draft_initial(url, str(org))
    return org


def settings(url, live=True):
    return Settings(database_url=url, dry_run=not live, outbound_enabled=live)


def sender(url, live=True):
    return SendingService(settings(url, live), __import__("flightbridge.compliance.rules", fromlist=["x"]).load_rules(),
                          FULL_PROFILE, FakeTransport)


def run(url, now=WED_10AM_TEXAS, live=True, dns=None):
    return autosend.step(settings(url, live), sender=sender(url, live), dns=dns or FakeDNS(), now=now,
                         rng=random.Random(1))


def q(url, sql, *a):
    with psycopg.connect(url) as c:
        return c.execute(sql, a).fetchall()


def test_off_does_nothing(ap):
    school(ap["url"])
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.system_setting SET value = value || '{\"enabled\": false}' WHERE key='send_autopilot'")
    assert run(ap["url"])["state"] == "off"
    assert q(ap["url"], "SELECT status FROM flightbridge.approval_task") == [("open",)]


def test_dry_run_engine_never_approves(ap):
    school(ap["url"])
    out = run(ap["url"], live=False)
    assert out["state"] == "waiting" and "DRY_RUN" in out["detail"]
    assert q(ap["url"], "SELECT status FROM flightbridge.approval_task") == [("open",)] and not FakeTransport.calls


def test_kill_switch_keeps_it_waiting(ap):
    school(ap["url"])
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.system_setting SET value='true'::jsonb WHERE key='outbound_kill_switch'")
    assert run(ap["url"])["state"] == "waiting" and not FakeTransport.calls


def test_sends_one_then_waits_for_the_gap(ap):
    school(ap["url"])
    school(ap["url"], "Gulf Coast Flight", "ops@gulf.example")
    out = run(ap["url"])
    assert out["state"] == "sent" and len(FakeTransport.calls) == 1
    req = FakeTransport.calls[0]
    assert req.subject == "Pilot training partnership for Lone Star Aviation"
    via = q(ap["url"], "SELECT decided_via, decided_by::text, status FROM flightbridge.approval_task WHERE status <> 'open'")
    assert via == [("autopilot", ap["emp"], "approved")]
    assert q(ap["url"], "SELECT status FROM flightbridge.outbound_message") == [("sent",)]
    # Message-ID kept for threading; follow-ups scheduled
    assert q(ap["url"], "SELECT rfc_message_id FROM flightbridge.email_message WHERE direction='outbound'") == [("<msg1@weoneaviation.in>",)]
    assert q(ap["url"], "SELECT count(*) FROM flightbridge.follow_up_task")[0][0] == 3
    st = q(ap["url"], "SELECT value FROM flightbridge.system_setting WHERE key='send_autopilot_state'")[0][0]
    assert st["first_sent_on"] == "2026-11-04" and st["preflight"]["ok"]
    assert run(ap["url"])["state"] == "gap" and len(FakeTransport.calls) == 1


def test_outside_school_hours_waits(ap):
    school(ap["url"])
    out = run(ap["url"], now=WED_MIDNIGHT_TEXAS)
    assert out["state"] == "waiting_hours" and not FakeTransport.calls


def test_warmup_limit(ap):
    cfg = {**autosend.DEFAULTS, "daily_limit": 40, "warmup": True}
    assert autosend.today_limit(cfg, {}, date(2026, 11, 4)) == (10, 1)
    assert autosend.today_limit(cfg, {"first_sent_on": "2026-11-01"}, date(2026, 11, 9)) == (20, 2)
    assert autosend.today_limit(cfg, {"first_sent_on": "2026-10-01"}, date(2026, 11, 9)) == (40, 6)
    assert autosend.today_limit({**cfg, "warmup": False, "daily_limit": 25}, {}, date(2026, 11, 9)) == (25, None)
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.system_setting SET value = value || '{\"warmup\": false, \"daily_limit\": 1}' WHERE key='send_autopilot'")
    school(ap["url"])
    school(ap["url"], "Gulf Coast Flight", "ops@gulf.example")
    assert run(ap["url"])["state"] == "sent"
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.system_setting SET value = value || '{\"next_send_at\": null}' WHERE key='send_autopilot_state'")
    assert run(ap["url"])["state"] == "done_today" and len(FakeTransport.calls) == 1


def test_no_approved_template_means_no_send(ap):
    school(ap["url"])
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.outreach_template SET status='archived'")
    out = run(ap["url"])
    assert out["state"] == "blocked" and "First email approved" in out["detail"] and not FakeTransport.calls


def test_unqualified_school_never_emailed(ap):
    school(ap["url"])
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.organisation SET eligibility='unqualified'")
    assert run(ap["url"])["state"] in ("idle", "drafted") and not FakeTransport.calls


def test_bounces_pause_until_resumed(ap):
    school(ap["url"])
    with psycopg.connect(ap["url"], autocommit=True) as c:
        th = c.execute("INSERT INTO flightbridge.email_thread (status) VALUES ('open') RETURNING id").fetchone()[0]
        for i in range(3):
            c.execute("""INSERT INTO flightbridge.email_message (thread_id, direction, from_address, subject, occurred_at, is_bounce)
                         VALUES (%s,'inbound','mailer-daemon@x.example','Undeliverable',%s,true)""", (th, WED_10AM_TEXAS))
    out = run(ap["url"])
    assert out["state"] == "paused" and "bounced" in out["detail"] and not FakeTransport.calls
    assert run(ap["url"])["state"] == "paused"
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("""UPDATE flightbridge.system_setting SET value = value || jsonb_build_object('resume_at', %s::text)
                     WHERE key='send_autopilot'""", (datetime(2026, 11, 4, 16, 30, tzinfo=timezone.utc).isoformat(),))
    out = run(ap["url"], now=datetime(2026, 11, 4, 16, 31, tzinfo=timezone.utc))
    assert out["state"] == "sent"


def test_mailbox_refusal_pauses(ap):
    school(ap["url"])
    FakeTransport.behaviour = "reject"
    assert run(ap["url"])["state"] == "failed"
    out = run(ap["url"], now=datetime(2026, 11, 4, 17, 0, tzinfo=timezone.utc))
    assert out["state"] == "paused" and "refused" in out["detail"]


def test_two_dmarc_records_block(ap):
    school(ap["url"])
    out = run(ap["url"], dns=FakeDNS(dmarc=("v=DMARC1; p=none", "v=DMARC1; p=reject")))
    assert out["state"] == "blocked" and "DMARC" in out["detail"] and not FakeTransport.calls
    pre = q(ap["url"], "SELECT value->'preflight' FROM flightbridge.system_setting WHERE key='send_autopilot_state'")[0][0]
    dm = next(c for c in pre["checks"] if c["id"] == "dmarc")
    assert dm["status"] == "fail" and "2 DMARC records" in dm["detail"] and "Delete all but one" in dm["fix"]


def test_preflight_runs_on_request_even_when_off(ap):
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("""UPDATE flightbridge.system_setting SET value = value || '{"enabled": false,
                     "check_requested_at": "2026-11-04T15:59:00+00:00"}' WHERE key='send_autopilot'""")
    assert run(ap["url"], dns=FakeDNS(spf=None, dkim=False))["state"] == "off"
    pre = q(ap["url"], "SELECT value->'preflight' FROM flightbridge.system_setting WHERE key='send_autopilot_state'")[0][0]
    st = {c["id"]: c["status"] for c in pre["checks"]}
    assert st["spf"] == "fail" and st["dkim"] == "fail" and st["dmarc"] == "pass" and st["mailbox"] == "pass"
    assert not pre["ok"]


def test_followups_auto_sent_in_thread(ap):
    school(ap["url"])
    emp = ap["emp"]
    t = save_template(ap["url"], "followup_1", "Following up: {{school_name}}",
                      "Dear {{school_name}} team,\n\nJust checking whether my earlier note reached you.", emp)
    approve_template(ap["url"], t["id"], emp)
    assert run(ap["url"])["state"] == "sent"
    from flightbridge import followups
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.follow_up_task SET due_at = now() - interval '1 minute' WHERE step = 1")
    assert [r["result"] for r in followups.process_due(ap["url"])] == ["drafted"]
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.system_setting SET value = value || '{\"next_send_at\": null}' WHERE key='send_autopilot_state'")
    out = run(ap["url"], now=datetime(2026, 11, 9, 16, 0, tzinfo=timezone.utc))
    assert out["state"] == "sent" and out["kind"] == "followup"
    fu = FakeTransport.calls[1]
    assert fu.subject == "Following up: Lone Star Aviation" and fu.in_reply_to == "<msg1@weoneaviation.in>"


def test_reply_drafts_are_never_auto_approved(ap):
    school(ap["url"])
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.ai_draft SET kind='reply'")
    assert run(ap["url"])["state"] in ("idle", "drafted") and not FakeTransport.calls


# ------------------------------------------------------------------------------------------- pure helpers
def test_spf_evaluation():
    dns = FakeDNS(spf="v=spf1 include:_spf.host.example -all")
    dns.records["_spf.host.example"] = ["v=spf1 ip4:203.0.113.0/24 ~all"]
    assert deliverability.spf_allows("weoneaviation.in", "203.0.113.5", dns) is True
    assert deliverability.spf_allows("weoneaviation.in", "198.51.100.1", dns) is False
    assert deliverability.spf_allows("weoneaviation.in", "203.0.113.5", FakeDNS(spf="v=spf1 +a +mx ~all")) is True


def test_template_spam_signals():
    assert deliverability.template_issues("Partnership for {{school_name}}", "Dear team, hello.") == []
    issues = deliverability.template_issues("FREE OFFER!!", "Act now: https://bit.ly/x and https://a.example")
    assert any("spam-trigger" in i for i in issues) and any("CAPITALS" in i for i in issues)
    assert any("links" in i for i in issues) and any("shortener" in i for i in issues)


def test_unsubscribe_headers():
    from flightbridge.sending.service import _SEND_CAPABILITY
    t = SmtpImapTransport(host="h", user="partnership@weoneaviation.in", password="p", capability=_SEND_CAPABILITY)
    m = t.build(SendRequest(to="a@b.example", subject="s", text="t", display_name="We One Aviation",
                            unsubscribe_url="https://x.example/u/FB-ABC", in_reply_to="<1@x>", references=("<0@x>",)))
    assert m["List-Unsubscribe"] == "<https://x.example/u/FB-ABC>, <mailto:partnership@weoneaviation.in?subject=unsubscribe>"
    assert m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click" and m["References"] == "<0@x> <1@x>"


def test_school_clock():
    assert str(school_tz("US", "CA")) == "America/Los_Angeles" and str(school_tz("AU", "Western Australia")) == "Australia/Perth"
    assert school_tz("XX") is None and not in_business_hours(WED_10AM_TEXAS, "XX")
    assert in_business_hours(WED_10AM_TEXAS, "US", "TX") and not in_business_hours(WED_10AM_TEXAS, "US", "CA")
    sat = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    assert business_time(sat, 0, "GB").weekday() == 0


def test_schools_needing_a_legal_check_are_left_for_a_person(ap):
    """UK needs human-verified facts: Auto-Pilot must not approve it (and then get it blocked); it waits in the queue."""
    school(ap["url"], "Airspace FTS", "info@airspace.example", region=None, country="GB")
    out = run(ap["url"])
    assert out["state"] == "idle" and out["held"] == {"GB": 1} and "legal check" in out["detail"]
    assert q(ap["url"], "SELECT status FROM flightbridge.approval_task") == [("open",)]
    assert q(ap["url"], "SELECT count(*) FROM flightbridge.outbound_message")[0][0] == 0 and not FakeTransport.calls


def test_us_school_sent_while_uk_school_waits(ap):
    school(ap["url"], "Airspace FTS", "info@airspace.example", region=None, country="GB")   # older, would be first
    school(ap["url"])
    out = run(ap["url"])
    assert out["state"] == "sent" and FakeTransport.calls[0].subject.endswith("Lone Star Aviation")
    rows = q(ap["url"], """SELECT g.country, a.status FROM flightbridge.approval_task a JOIN flightbridge.ai_draft d ON d.id=a.draft_id
                           JOIN flightbridge.organisation g ON g.id=d.org_id ORDER BY 1""")
    assert rows == [("GB", "open"), ("US", "approved")]


def test_gate_block_after_precheck_pauses_with_reason(ap):
    import dataclasses
    school(ap["url"])
    bad = dataclasses.replace(FULL_PROFILE, ad_disclosure_text=None)          # US content rule can't pass
    svc = SendingService(settings(ap["url"]), __import__("flightbridge.compliance.rules", fromlist=["x"]).load_rules(),
                         bad, FakeTransport)
    out = autosend.step(settings(ap["url"]), sender=svc, dns=FakeDNS(), now=WED_10AM_TEXAS, rng=random.Random(1))
    assert out["state"] == "blocked" and not FakeTransport.calls
    st = q(ap["url"], "SELECT value FROM flightbridge.system_setting WHERE key='send_autopilot_state'")[0][0]
    assert st["paused_at"] and "Nothing was sent" in st["pause_reason"]
    assert autosend.step(settings(ap["url"]), sender=svc, dns=FakeDNS(), now=WED_10AM_TEXAS,
                         rng=random.Random(1))["state"] == "paused"


def _mac(url, seconds_ago):
    from datetime import timedelta
    at = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('engine_status', %s::jsonb)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps({"at": at, "runner": "mac-mini"}),))


def _cloud(url, monkeypatch, minutes=0.0):
    from flightbridge import worker
    monkeypatch.setattr(worker.db, "ensure_schema", lambda url: "current")      # the test schema is already in place
    monkeypatch.setattr(worker.quota, "install", lambda url: None)
    monkeypatch.setattr(worker, "autosend_step", lambda s, sender=None, dns=None: autosend.step(
        s, sender=sender, dns=FakeDNS(), now=WED_10AM_TEXAS, rng=random.Random(1)))
    t = iter(range(0, 10_000, 30))
    return worker.cloud_send(settings(url), minutes=minutes, every=30, clock=lambda: next(t), sleep=lambda _: None,
                             sender=sender(url), reader=None)


def test_cloud_does_nothing_while_the_mac_is_online(ap, monkeypatch):
    school(ap["url"])
    _mac(ap["url"], 20)
    assert _cloud(ap["url"], monkeypatch)["state"] == "mac_online" and not FakeTransport.calls
    assert q(ap["url"], "SELECT value->>'state' FROM flightbridge.system_setting WHERE key='cloud_sender'") == [("mac_online",)]


def test_cloud_sends_when_the_mac_is_off(ap, monkeypatch):
    school(ap["url"])
    school(ap["url"], "Gulf Coast Flight", "ops@gulf.example")
    _mac(ap["url"], 3600)
    out = _cloud(ap["url"], monkeypatch, minutes=1.0)
    assert out["state"] == "ran" and out["sent"] == 1 and len(FakeTransport.calls) == 1   # then the gap holds it
    assert out["steps"].get("gap", 0) >= 1


def test_cloud_respects_autopilot_off(ap, monkeypatch):
    school(ap["url"])
    with psycopg.connect(ap["url"], autocommit=True) as c:
        c.execute("UPDATE flightbridge.system_setting SET value = value || '{\"enabled\": false}' WHERE key='send_autopilot'")
    assert _cloud(ap["url"], monkeypatch)["state"] == "autopilot_off" and not FakeTransport.calls


def test_us_state_full_names_map_to_their_own_time_zone():
    from flightbridge.localtime import school_tz
    assert str(school_tz("US", "Virginia")) == "America/New_York"
    assert str(school_tz("US", "Utah")) == "America/Denver"
    assert str(school_tz("US", "Alaska")) == "America/Anchorage"
    assert str(school_tz("US", "Georgia")) == "America/New_York"
    assert str(school_tz("US", "Oklahoma")) == "America/Chicago"
