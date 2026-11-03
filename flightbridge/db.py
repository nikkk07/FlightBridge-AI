"""Thin psycopg helpers. Every unit of work is one transaction (commit on success, rollback on error)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent / "migrations"   # shipped inside the package


@contextmanager
def tx(database_url: str):
    with psycopg.connect(database_url, prepare_threshold=None) as conn:   # pgbouncer-safe, like the CRM
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def audit(conn, action: str, entity: str, entity_id, payload: dict | None = None,
          actor_type: str = "system", actor_id=None) -> None:
    conn.execute(
        "INSERT INTO flightbridge.audit_log (actor_type, actor_id, action, entity, entity_id, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (actor_type, actor_id, action, entity, str(entity_id) if entity_id is not None else None,
         json.dumps(payload or {}, default=str)),
    )


def migrations_digest() -> str:
    import hashlib
    h = hashlib.sha256()
    for f in sorted(MIGRATIONS.glob("*.sql")):
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def migrate(database_url: str) -> list[str]:
    """Apply every migration in one transaction. All files are idempotent and schema-qualified; a transaction-level
    advisory lock (safe through pgbouncer) stops two servers starting at once from racing."""
    applied = []
    with tx(database_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('flightbridge_migrate'))")
        for f in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(f.read_text(encoding="utf-8"))
            applied.append(f.name)
        conn.execute("""INSERT INTO flightbridge.system_setting (key, value, updated_at) VALUES ('schema_files', %s, now())
                        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                     (json.dumps({"digest": migrations_digest(), "files": applied}),))
    return applied


def ensure_schema(database_url: str) -> str:
    """Bring the FlightBridge schema up to date if this package version's migrations aren't installed yet.
    Cheap when up to date (one SELECT). Returns 'current' or 'migrated'."""
    want = migrations_digest()
    try:
        with tx(database_url) as conn:
            row = conn.execute("SELECT value->>'digest' FROM flightbridge.system_setting WHERE key='schema_files'").fetchone()
        if row and row[0] == want:
            return "current"
    except psycopg.errors.UndefinedTable:
        pass
    except psycopg.errors.InvalidSchemaName:
        pass
    migrate(database_url)
    return "migrated"
