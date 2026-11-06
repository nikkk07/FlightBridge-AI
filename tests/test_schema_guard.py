"""ensure_schema: never steps back after a newer engine migrated, and gives way when tables are busy (no deadlocks)."""
import json
import threading
import time

import psycopg

from flightbridge import db


def _set_files(url, files, digest="older"):
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('schema_files', %s::jsonb)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps({"digest": digest, "files": files}),))


def test_newer_engine_is_not_undone(clean_db):
    mine = sorted(f.name for f in db.MIGRATIONS.glob("*.sql"))
    _set_files(clean_db, mine + ["099_from_a_newer_engine.sql"], "newer-digest")
    assert db.ensure_schema(clean_db) == "newer"
    with psycopg.connect(clean_db) as c:
        assert c.execute("SELECT value->>'digest' FROM flightbridge.system_setting WHERE key='schema_files'").fetchone()[0] == "newer-digest"


def test_busy_tables_mean_later_not_deadlock(clean_db, monkeypatch):
    db._last_failure["at"] = 0.0
    _set_files(clean_db, ["001_flightbridge_schema.sql"])          # an older engine wrote last: we must migrate
    hold = threading.Event()
    done = threading.Event()

    def busy():                                                    # someone is reading organisation in a long transaction
        with psycopg.connect(clean_db) as c:
            c.execute("LOCK TABLE flightbridge.organisation IN ACCESS SHARE MODE")
            hold.set()
            done.wait(15)

    t = threading.Thread(target=busy)
    t.start()
    hold.wait(5)
    t0 = time.time()
    try:
        assert db.ensure_schema(clean_db) == "later"
        assert time.time() - t0 < 30
        assert db.ensure_schema(clean_db) == "later"             # backs off: no retry for 10 minutes
    finally:
        done.set()
        t.join()
    db._last_failure["at"] = 0.0
    assert db.ensure_schema(clean_db) == "migrated"
    assert db.ensure_schema(clean_db) == "current"
