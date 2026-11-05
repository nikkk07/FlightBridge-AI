"""Second mailbox (HIOX India, SMTP + IMAP) and the 'Send emails from' choice."""
import json
import smtplib

import psycopg
import pytest

from flightbridge.sending import service, transports
from flightbridge.sending.transports import SendRequest

from .test_sending_db import FakeTransport, release_kill_switch, seed, status, svc


def test_smtp_transport_needs_the_capability():
    with pytest.raises(PermissionError):
        transports.SmtpImapTransport(host="h", user="u@x.in", password="p", capability=object())


class FakeSMTP:
    sent, logins = [], []
    mode = "ok"

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSMTP.mode == "unreachable":
            raise OSError("connection refused")
        self.host, self.port = host, port

    def login(self, user, password):
        if FakeSMTP.mode == "badpass":
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")
        FakeSMTP.logins.append(user)

    def send_message(self, msg):
        if FakeSMTP.mode == "refused":
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        FakeSMTP.sent.append(msg)
        return {}

    def quit(self):
        pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.mode = [], [], "ok"
    monkeypatch.setattr(transports.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(transports.SmtpImapTransport, "_save_copy", lambda self, msg: None)
    return FakeSMTP


def make():
    return transports.SmtpImapTransport(host="host2.dnsinweb.com", user="partnership@weoneaviation.in", password="pw",
                                        capability=service._SEND_CAPABILITY)


def test_smtp_send_builds_a_proper_message(smtp):
    make().send(SendRequest(to="info@school.example", subject="Hello", text="Dear team,\n\nRef: FB-1",
                            display_name="We One Aviation", in_reply_to="<abc@school.example>"))
    m = smtp.sent[0]
    assert m["From"] == "We One Aviation <partnership@weoneaviation.in>" and m["To"] == "info@school.example"
    assert m["In-Reply-To"] == "<abc@school.example>" and m["Message-ID"].endswith("@weoneaviation.in>")
    assert "Ref: FB-1" in m.get_content() and smtp.logins == ["partnership@weoneaviation.in"]


@pytest.mark.parametrize("mode,exc", [("unreachable", transports.NotSent), ("badpass", transports.DefiniteRejection),
                                      ("refused", transports.DefiniteRejection)])
def test_smtp_failures_are_classified(smtp, mode, exc):
    smtp.mode = mode
    with pytest.raises(exc):
        make().send(SendRequest(to="a@b.example", subject="s", text="t", display_name="n"))


def _send_from_hiox(url):
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('integrations', %s::jsonb)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                  (json.dumps({"send_from": "hiox", "hiox_mail_user": "partnership@weoneaviation.in",
                               "hiox_mail_host": "host2.dnsinweb.com"}),))
    from flightbridge import secrets
    secrets._cache.clear()


def test_mailbox_choice_and_sent_record(clean_db, rules):
    _send_from_hiox(clean_db)
    s = svc(clean_db, rules)
    assert s.mailbox()["label"] == "imap:partnership@weoneaviation.in@host2.dnsinweb.com"
    seed(clean_db)
    release_kill_switch(clean_db)
    FakeTransport.calls = []
    out = svc(clean_db, rules, dry_run=False, enabled=True, factory=lambda capability: FakeTransport(capability)).process_ready()
    assert [o.status for o in out] == ["sent"]
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT mailbox FROM flightbridge.email_message WHERE direction='outbound'").fetchone()[0] == \
            "imap:partnership@weoneaviation.in@host2.dnsinweb.com"


def test_unreachable_mail_server_defers_and_rechecks_later(clean_db, rules):
    _send_from_hiox(clean_db)
    seed(clean_db)
    release_kill_switch(clean_db)

    class Unreachable:
        def __init__(self, capability): pass

        def send(self, req):
            raise transports.NotSent("connect:OSError")
    first = svc(clean_db, rules, dry_run=False, enabled=True, factory=lambda capability: Unreachable(capability)).process_ready()
    assert [o.status for o in first] == ["deferred"]
    assert status(clean_db, first[0].outbound_id) == "pending_checks"          # every check runs again next time
    FakeTransport.calls = []
    again = svc(clean_db, rules, dry_run=False, enabled=True, factory=lambda capability: FakeTransport(capability)).process_ready()
    assert [o.status for o in again] == ["sent"] and len(FakeTransport.calls) == 1


def test_only_transports_and_the_key_test_use_smtp():
    import pathlib
    pkg = pathlib.Path(service.__file__).resolve().parents[1]
    users = sorted(f.relative_to(pkg).as_posix() for f in pkg.rglob("*.py") if "smtplib" in f.read_text())
    assert users == ["integrations.py", "sending/transports.py"]
