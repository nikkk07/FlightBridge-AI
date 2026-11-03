"""Keys entered in the app: encrypted at rest, env overrides, never readable after a server-secret change; engine lock."""
import psycopg
import pytest

from flightbridge import automation, secrets
from flightbridge.research import places
from flightbridge.settings import Settings
from flightbridge.worker import run_once


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "server-secret-1")
    monkeypatch.delenv("FB_SECRETS_KEY", raising=False)
    for env in secrets.NAMES.values():
        monkeypatch.delenv(env, raising=False)
    secrets.configure(None)
    yield
    secrets.configure(None)


def _save(url, name, value):
    with psycopg.connect(url) as c:
        secrets.save(c, name, value)
        c.commit()


def test_saved_key_is_encrypted_and_readable_by_the_engine(clean_db):
    _save(clean_db, "google_places_api_key", "AIzaSy-test-key-1234")
    with psycopg.connect(clean_db) as c:
        ct, l4 = c.execute("SELECT ciphertext, last4 FROM flightbridge.integration_secret").fetchone()
    assert "AIzaSy" not in ct and l4 == "1234"
    assert secrets.get("google_places_api_key", clean_db) == "AIzaSy-test-key-1234"


def test_environment_variable_wins(clean_db, monkeypatch):
    _save(clean_db, "groq_api_key", "gsk_saved_in_app_000")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
    assert secrets.get("groq_api_key", clean_db) == "gsk_from_env"


def test_changed_server_secret_makes_key_unreadable_not_wrong(clean_db, monkeypatch):
    _save(clean_db, "gemini_api_key", "AIza-gemini-abcd")
    monkeypatch.setenv("SECRET_KEY", "rotated")
    secrets.configure(clean_db)          # clears cache
    assert secrets.get("gemini_api_key") is None
    with pytest.raises(secrets.SecretUnreadable):
        with psycopg.connect(clean_db) as c:
            secrets.decrypt(c.execute("SELECT ciphertext FROM flightbridge.integration_secret").fetchone()[0])


def test_places_search_uses_the_key_saved_in_the_app(clean_db):
    _save(clean_db, "google_places_api_key", "AIza-app-key-9999")
    secrets.configure(clean_db)

    class S:
        def post(self, url, **kw):
            self.headers = kw["headers"]

            class R:
                status_code = 200
                def json(self): return {"places": []}
            return R()
    s = S()
    places.search("X Aero", None, "GB", session=s, field_mask="places.id")
    assert s.headers["X-Goog-Api-Key"] == "AIza-app-key-9999" and s.headers["X-Goog-FieldMask"] == "places.id"


def test_llm_provider_from_app_settings(clean_db):
    from flightbridge.ai import llm
    _save(clean_db, "groq_api_key", "gsk_app_key_5678")
    with psycopg.connect(clean_db, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('integrations', '{"llm_provider": "groq"}')
                     ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""")
    secrets.configure(clean_db)
    name, url, key, model = llm.provider()
    assert (name, key, model) == ("groq", "gsk_app_key_5678", "llama-3.1-8b-instant")


def test_only_one_engine_cycle_at_a_time(clean_db, monkeypatch):
    assert automation.acquire_engine(clean_db)
    monkeypatch.setattr(automation, "_holder", lambda: "someone-else")
    assert not automation.acquire_engine(clean_db)
    out = run_once(Settings(database_url=clean_db), reader=None)
    assert out == {"skipped": "another FlightBridge cycle is running"}
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT count(*) FROM flightbridge.worker_run").fetchone()[0] == 0


def test_lock_is_released_after_a_cycle(clean_db):
    run_once(Settings(database_url=clean_db), reader=None)
    with psycopg.connect(clean_db) as c:
        until = c.execute("SELECT lease_until <= now() FROM flightbridge.engine_lease WHERE name='engine'").fetchone()[0]
    assert until is True


def test_engine_installs_its_own_schema_upgrades(clean_db):
    from flightbridge import db
    assert db.ensure_schema(clean_db) in ("current", "migrated")
    assert db.ensure_schema(clean_db) == "current"                       # second call is a single SELECT
    with psycopg.connect(clean_db, autocommit=True) as c:                # simulate an older install (no 041 column)
        c.execute("ALTER TABLE flightbridge.job_request DROP COLUMN area_id CASCADE")
        c.execute("UPDATE flightbridge.system_setting SET value='{\"digest\": \"old\"}' WHERE key='schema_files'")
    assert db.ensure_schema(clean_db) == "migrated"
    with psycopg.connect(clean_db) as c:
        assert c.execute("""SELECT count(*) FROM information_schema.columns WHERE table_schema='flightbridge'
                            AND table_name='job_request' AND column_name='area_id'""").fetchone()[0] == 1
