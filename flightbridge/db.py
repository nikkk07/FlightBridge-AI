"""Thin psycopg helpers. Every unit of work is one transaction (commit on success, rollback on error)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


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


def migrate(database_url: str) -> list[str]:
    applied = []
    with tx(database_url) as conn:
        for f in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(f.read_text(encoding="utf-8"))
            applied.append(f.name)
    return applied
