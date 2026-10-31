import pytest

from flightbridge.settings import Settings
from flightbridge.worker import build_reader, run_once


class BrokenReader:
    mailbox = "hostinger:x"

    def fetch_since(self, *a, **k):
        raise ConnectionError("inbox down")


def test_inbox_outage_does_not_stop_the_cycle(clean_db, rules):
    out = run_once(Settings(database_url=clean_db), reader=BrokenReader())
    assert "inbox down" in out["inbound_error"] and out["followups"] == [] and out["outbound"] == []
    assert out["dry_run"] is True and out["outbound_enabled"] is False


@pytest.mark.parametrize("env,err", [({"INBOUND_SOURCE": "hostinger"}, "HOSTINGER_MAIL_API_TOKEN"),
                                     ({"INBOUND_SOURCE": "imap"}, "IMAP_HOST")])
def test_reader_config_errors_are_explicit(env, err):
    with pytest.raises(ValueError, match=err):
        build_reader(Settings.from_env({"DATABASE_URL": "x", **env}))


def test_no_inbound_source_means_no_reader():
    assert build_reader(Settings.from_env({"DATABASE_URL": "x"})) is None
