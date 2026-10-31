import ast
import pathlib

import pytest

from flightbridge.compliance.suppression import SharedProviderDomainError, SuppressionIndex, email_hash
from flightbridge.settings import Settings

PKG = pathlib.Path(__file__).resolve().parents[1] / "flightbridge"


@pytest.mark.parametrize("env,dry,enabled", [
    ({}, True, False),
    ({"DRY_RUN": "False"}, True, False),         # only exact "false" disables dry-run
    ({"DRY_RUN": "0"}, True, False),
    ({"OUTBOUND_ENABLED": "TRUE"}, True, False),  # only exact "true" enables outbound
    ({"OUTBOUND_ENABLED": "1"}, True, False),
    ({"DRY_RUN": "false", "OUTBOUND_ENABLED": "true"}, False, True),
])
def test_flag_parsing_is_fail_safe(env, dry, enabled):
    s = Settings.from_env(env)
    assert (s.dry_run, s.outbound_enabled) == (dry, enabled)
    assert s.may_transmit == (not dry and enabled)


def test_only_sending_service_imports_transports():
    offenders = []
    for f in PKG.rglob("*.py"):
        rel = f.relative_to(PKG).as_posix()
        if rel in ("sending/service.py", "sending/transports.py"):
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("transports" in n for n in names):
                offenders.append(rel)
    assert offenders == []


def test_transport_refuses_construction_without_capability():
    from flightbridge.sending.transports import HostingerMailApiTransport
    with pytest.raises(PermissionError):
        HostingerMailApiTransport(base_url="https://x", token="t", mailbox_id="m", capability=object())


def test_no_module_calls_requests_post_except_transport():  # tests may stub .post
    for f in PKG.rglob("*.py"):
        if f.name == "transports.py":
            continue
        assert ".post(" not in f.read_text(), f


def test_suppression_scopes():
    s = SuppressionIndex(contact_hashes=[email_hash("a@school.example")], org_ids=["org-9"], domains=["blocked.example"])
    assert s.match("A@School.example ", "org-1") == "contact"
    assert s.match("ops@other.example", "org-9") == "organisation"
    assert s.match("x@blocked.example", "org-2") == "domain"
    assert s.match("x@fine.example", "org-2") is None


def test_shared_provider_domain_cannot_be_domain_suppressed():
    with pytest.raises(SharedProviderDomainError):
        SuppressionIndex(domains=["gmail.com"])
    s = SuppressionIndex(contact_hashes=[email_hash("school1@gmail.com")])
    assert s.match("school2@gmail.com", "org-2") is None   # unrelated Gmail user unaffected


def test_sent_folder_resolved_by_special_use_not_hardcoded():
    from flightbridge.sending import service, transports

    class Resp:
        def __init__(self, data): self._d = data
        def raise_for_status(self): pass
        def json(self): return self._d

    t = transports.HostingerMailApiTransport(base_url="https://x", token="t", mailbox_id="AC1",
                                             capability=service._SEND_CAPABILITY)
    calls = []
    folders = {"data": [{"path": "INBOX", "specialUse": "\\Inbox"}, {"path": "INBOX.Sent", "specialUse": "\\Sent"}]}
    t._s.get = lambda url, **k: (calls.append(url), Resp(folders))[1]
    t._s.post = lambda url, **k: (calls.append(url), Resp({"data": [{"uid": 1}]}))[1]
    assert t.special_folder("\\Sent") == "INBOX.Sent"
    assert t.find_sent_by_ref("FB-0123456789AB") is True
    assert calls[-1].endswith("/folders/INBOX.Sent/messages/search")
