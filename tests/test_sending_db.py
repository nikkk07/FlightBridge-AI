"""End-to-end send-path tests against a real Postgres (schema + triggers + service)."""
import uuid

import psycopg
import pytest

from flightbridge.compliance.content import content_sha256
from flightbridge.compliance.suppression import email_domain, email_hash
from flightbridge.sending.service import SendingService
from flightbridge.settings import Settings

from .conftest import FULL_PROFILE

pytestmark = pytest.mark.usefixtures("clean_db")


class FakeTransport:
    calls = []

    def __init__(self, capability, behaviour="ok"):
        self.behaviour = behaviour

    def send(self, req):
        FakeTransport.calls.append(req)
        if self.behaviour == "timeout":
            from flightbridge.sending.transports import TransportError
            raise TransportError("network:ReadTimeout")
        if self.behaviour == "reject":
            from flightbridge.sending.transports import DefiniteRejection
            raise DefiniteRejection("http_422")


def exploding_factory(**_):
    raise AssertionError("transport must not be constructed")


def conn(url):
    return psycopg.connect(url, autocommit=True)


def seed(url, country="US", email="info@school.example", approve=True, org_name="Test Aero Academy"):
    with conn(url) as c:
        emp = c.execute("INSERT INTO public.employee (name, department) VALUES ('Admin','Admin') RETURNING id").fetchone()[0]
        org = c.execute("INSERT INTO flightbridge.organisation (name, country, status) VALUES (%s,%s,'qualified') RETURNING id",
                        (org_name, country)).fetchone()[0]
        con = c.execute("""INSERT INTO flightbridge.contact (org_id,email,email_hash,email_domain,is_role_address,source_url)
                           VALUES (%s,%s,%s,%s,true,'https://school.example/contact') RETURNING id""",
                        (org, email, email_hash(email), email_domain(email))).fetchone()[0]
        d = c.execute("""INSERT INTO flightbridge.ai_draft (org_id,contact_id,kind,risk,subject,body_text,generator)
                         VALUES (%s,%s,'initial','AMBER','Student referral partnership enquiry','Dear team,\n\nHello.','t')
                         RETURNING id""", (org, con)).fetchone()[0]
        t = c.execute("INSERT INTO flightbridge.approval_task (draft_id,risk,escalation_reason) VALUES (%s,'AMBER','x') RETURNING id",
                      (d,)).fetchone()[0]
        if approve:
            c.execute("""UPDATE flightbridge.approval_task SET status='approved', decided_by=%s, decided_at=now(),
                         final_subject='Student referral partnership enquiry', final_body='Dear team,\n\nHello.' WHERE id=%s""",
                      (emp, t))
    return {"emp": emp, "org": org, "contact": con, "task": str(t)}


def svc(url, rules, dry_run=True, enabled=False, factory=exploding_factory, profile=FULL_PROFILE):
    return SendingService(Settings(database_url=url, dry_run=dry_run, outbound_enabled=enabled), rules, profile, factory)


def status(url, oid):
    with conn(url) as c:
        return c.execute("SELECT status FROM flightbridge.outbound_message WHERE id=%s", (oid,)).fetchone()[0]


def release_kill_switch(url):
    with conn(url) as c:
        c.execute("UPDATE flightbridge.system_setting SET value='false'::jsonb WHERE key='outbound_kill_switch'")


def test_dry_run_default_renders_and_never_builds_transport(clean_db, rules):
    s = seed(clean_db)
    out = svc(clean_db, rules).process_ready()
    assert [o.status for o in out] == ["dry_run_rendered"] and out[0].reasons == ["DRY_RUN"]


def test_outbound_enabled_but_kill_switch_engaged_still_dry(clean_db, rules):
    seed(clean_db)
    out = svc(clean_db, rules, dry_run=False, enabled=True).process_ready()
    assert out[0].status == "dry_run_rendered" and out[0].reasons == ["kill_switch"]


def test_dry_run_false_but_outbound_disabled_still_dry(clean_db, rules):
    seed(clean_db)
    release_kill_switch(clean_db)
    out = svc(clean_db, rules, dry_run=False, enabled=False).process_ready()
    assert out[0].reasons == ["OUTBOUND_ENABLED=false"]


def test_fully_enabled_sends_exactly_once(clean_db, rules):
    seed(clean_db)
    release_kill_switch(clean_db)
    FakeTransport.calls = []
    service = svc(clean_db, rules, dry_run=False, enabled=True, factory=lambda capability: FakeTransport(capability))
    first = service.process_ready()
    assert first[0].status == "sent" and len(FakeTransport.calls) == 1
    again = service.process(first[0].outbound_id)
    assert again.reasons == ["already_processed"] and len(FakeTransport.calls) == 1
    assert service.process_ready() == [] and len(FakeTransport.calls) == 1
    assert "Ref: FB-" in FakeTransport.calls[0].text and FULL_PROFILE.opt_out_text in FakeTransport.calls[0].text


def test_timeout_becomes_send_unknown_and_is_not_retried(clean_db, rules):
    seed(clean_db)
    release_kill_switch(clean_db)
    FakeTransport.calls = []
    service = svc(clean_db, rules, dry_run=False, enabled=True,
                  factory=lambda capability: FakeTransport(capability, "timeout"))
    out = service.process_ready()
    assert out[0].status == "send_unknown"
    service.process_ready()
    service.process(out[0].outbound_id)
    assert len(FakeTransport.calls) == 1


def test_definite_rejection_marks_failed(clean_db, rules):
    seed(clean_db)
    release_kill_switch(clean_db)
    out = svc(clean_db, rules, dry_run=False, enabled=True,
              factory=lambda capability: FakeTransport(capability, "reject")).process_ready()
    assert out[0].status == "failed", out[0].reasons


def test_open_task_is_never_enqueued(clean_db, rules):
    s = seed(clean_db, approve=False)
    service = svc(clean_db, rules)
    assert service.enqueue_from_approval(s["task"]) is None
    assert service.process_ready() == []


def test_enqueue_is_idempotent(clean_db, rules):
    s = seed(clean_db)
    service = svc(clean_db, rules)
    a, b = service.enqueue_from_approval(s["task"]), service.enqueue_from_approval(s["task"])
    assert a == b
    with conn(clean_db) as c:
        assert c.execute("SELECT count(*) FROM flightbridge.outbound_message").fetchone()[0] == 1


def test_outbound_without_approval_task_is_blocked(clean_db, rules):
    s = seed(clean_db, approve=False)
    with conn(clean_db) as c:
        body = "Dear team"
        oid = c.execute("""INSERT INTO flightbridge.outbound_message (idempotency_key,contact_id,org_id,kind,subject,body_text,
                             content_sha256,ref_token) VALUES ('manual:1',%s,%s,'initial','S',%s,%s,'FB-000000000001') RETURNING id""",
                        (s["contact"], s["org"], body, content_sha256("S", body))).fetchone()[0]
    out = svc(clean_db, rules).process(str(oid))
    assert out.status == "blocked" and "human_approval_missing_or_mismatched" in out.reasons


def test_real_signed_off_profile_renders_us_outreach_in_dry_run(clean_db, rules):
    from flightbridge.settings import load_sender_profile
    seed(clean_db)
    out = svc(clean_db, rules, profile=load_sender_profile()).process_ready()
    assert out[0].status == "dry_run_rendered", out[0].reasons


def test_canada_without_human_evidence_needs_review(clean_db, rules):
    seed(clean_db, country="CA")
    out = svc(clean_db, rules).process_ready()
    assert out[0].status == "needs_review"


def test_contact_suppression_blocks(clean_db, rules):
    s = seed(clean_db)
    with conn(clean_db) as c:
        c.execute("INSERT INTO flightbridge.suppression (scope,email_hash,reason) VALUES ('contact',%s,'unsubscribe')",
                  (email_hash("info@school.example"),))
    out = svc(clean_db, rules).process_ready()
    assert out[0].status == "blocked" and "suppressed:contact" in out[0].reasons


def test_org_suppression_does_not_affect_other_org_on_shared_provider(clean_db, rules):
    a = seed(clean_db, email="schoola@gmail.com", org_name="A")
    with conn(clean_db) as c:
        c.execute("INSERT INTO flightbridge.suppression (scope,org_id,reason) VALUES ('organisation',%s,'rejection')", (a["org"],))
    seed(clean_db, email="schoolb@gmail.com", org_name="B")
    res = {o.status for o in svc(clean_db, rules).process_ready()}
    assert res == {"blocked", "dry_run_rendered"}


def test_db_rejects_domain_suppression_of_shared_provider(clean_db):
    with conn(clean_db) as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("INSERT INTO flightbridge.suppression (scope,domain,reason) VALUES ('domain','gmail.com','manual')")


def test_db_scope_columns_enforced(clean_db):
    with conn(clean_db) as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO flightbridge.suppression (scope,domain,email_hash,reason) VALUES ('domain','x.example','h','manual')")


@pytest.mark.parametrize("table", ["suppression", "audit_log", "compliance_check"])
def test_append_only_tables(clean_db, rules, table):
    seed(clean_db)
    svc(clean_db, rules).process_ready()
    with conn(clean_db) as c:
        if table == "suppression":
            c.execute("INSERT INTO flightbridge.suppression (scope,domain,reason) VALUES ('domain','x.example','manual')")
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute(f"DELETE FROM flightbridge.{table}")


def test_db_trigger_refuses_authorization_without_passing_checks(clean_db, rules):
    s = seed(clean_db)
    oid = svc(clean_db, rules).enqueue_from_approval(s["task"])
    with conn(clean_db) as c, pytest.raises(psycopg.errors.RaiseException):
        # a rogue code path trying to skip the gates
        c.execute("UPDATE flightbridge.outbound_message SET status='authorized', authorized_at=now(), "
                  "eligibility_check_id=NULL WHERE id=%s", (oid,))


def test_db_trigger_refuses_failing_check(clean_db, rules):
    s = seed(clean_db)
    oid = svc(clean_db, rules).enqueue_from_approval(s["task"])
    with conn(clean_db) as c:
        e = c.execute("INSERT INTO flightbridge.compliance_check (outbound_id,kind,decision,rules_version) "
                      "VALUES (%s,'jurisdiction','HUMAN_REVIEW','x') RETURNING id", (oid,)).fetchone()[0]
        k = c.execute("INSERT INTO flightbridge.compliance_check (outbound_id,kind,decision,rules_version,content_sha256) "
                      "SELECT %s,'content','PASS','x',content_sha256 FROM flightbridge.outbound_message WHERE id=%s RETURNING id",
                      (oid, oid)).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE flightbridge.outbound_message SET status='authorized', authorized_at=now(), "
                      "eligibility_check_id=%s, content_check_id=%s WHERE id=%s", (e, k, oid))


def test_db_trigger_refuses_check_from_another_message(clean_db, rules):
    s = seed(clean_db)
    oid = svc(clean_db, rules).enqueue_from_approval(s["task"])
    other = uuid.uuid4()
    with conn(clean_db) as c:
        e = c.execute("INSERT INTO flightbridge.compliance_check (outbound_id,kind,decision,rules_version) "
                      "VALUES (%s,'jurisdiction','ALLOW','x') RETURNING id", (other,)).fetchone()[0]
        k = c.execute("INSERT INTO flightbridge.compliance_check (outbound_id,kind,decision,rules_version,content_sha256) "
                      "SELECT %s,'content','PASS','x',content_sha256 FROM flightbridge.outbound_message WHERE id=%s RETURNING id",
                      (oid, oid)).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE flightbridge.outbound_message SET status='authorized', authorized_at=now(), "
                      "eligibility_check_id=%s, content_check_id=%s WHERE id=%s", (e, k, oid))


def test_content_is_immutable_after_enqueue(clean_db, rules):
    s = seed(clean_db)
    oid = svc(clean_db, rules).enqueue_from_approval(s["task"])
    with conn(clean_db) as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE flightbridge.outbound_message SET body_text='tampered' WHERE id=%s", (oid,))


def test_terminal_status_cannot_be_reopened(clean_db, rules):
    seed(clean_db)
    out = svc(clean_db, rules).process_ready()
    with conn(clean_db) as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE flightbridge.outbound_message SET status='pending_checks' WHERE id=%s", (out[0].outbound_id,))


def test_second_initial_to_same_contact_rejected(clean_db, rules):
    s = seed(clean_db)
    svc(clean_db, rules).enqueue_from_approval(s["task"])
    with conn(clean_db) as c, pytest.raises(psycopg.errors.UniqueViolation):
        c.execute("""INSERT INTO flightbridge.outbound_message (idempotency_key,contact_id,org_id,kind,subject,body_text,
                       content_sha256,ref_token) VALUES ('other',%s,%s,'initial','S','B','h','FB-00000000000F')""",
                  (s["contact"], s["org"]))


def test_every_decision_is_audited(clean_db, rules):
    seed(clean_db)
    svc(clean_db, rules).process_ready()
    with conn(clean_db) as c:
        actions = {r[0] for r in c.execute("SELECT action FROM flightbridge.audit_log").fetchall()}
    assert {"outbound_enqueued", "outbound_authorized", "outbound_dry_run_rendered"} <= actions
