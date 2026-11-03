"""API keys and integration settings entered in the app.

Lookup order for every key: environment variable (if set) -> encrypted value saved in the app -> None.
So a deployment can still pin a key in its environment, and nothing breaks when a key is missing.

Encryption: Fernet (AES-128-CBC + HMAC-SHA256). The Fernet key is derived from FB_SECRETS_KEY, or from the CRM's
SECRET_KEY when FB_SECRETS_KEY is unset. Changing that server secret makes saved keys unreadable; the app then
says so and asks for the key again (it never guesses).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time

from . import db

NAMES = {
    "google_places_api_key": "GOOGLE_PLACES_API_KEY",
    "groq_api_key": "GROQ_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "hostinger_mail_api_token": "HOSTINGER_MAIL_API_TOKEN",
    "hostinger_mailbox_resource_id": "HOSTINGER_MAILBOX_RESOURCE_ID",
}
DEFAULT_SETTINGS = {"engine_in_crm": True, "llm_provider": "none", "llm_model": "", "inbound_source": "none",
                    "store_google_contacts": True}
_CACHE_SECONDS = 60
_database_url: str | None = None
_cache: dict = {}


class SecretUnreadable(RuntimeError):
    pass


def configure(database_url: str | None) -> None:
    """Point lookups at a database (the worker calls this at the start of each cycle)."""
    global _database_url
    _database_url = database_url or None
    _cache.clear()


def _fernet():
    from cryptography.fernet import Fernet
    master = os.environ.get("FB_SECRETS_KEY") or os.environ.get("SECRET_KEY")
    if not master:
        raise SecretUnreadable("FB_SECRETS_KEY or SECRET_KEY must be set on the server to store API keys")
    key = base64.urlsafe_b64encode(hashlib.sha256(b"flightbridge-secrets-v1|" + master.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable("saved key can't be read (the server secret changed) - enter it again") from exc


def last4(value: str) -> str:
    v = value.strip()
    return v[-4:] if len(v) >= 8 else "•" * len(v)


def _cached(key, loader):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    val = loader()
    _cache[key] = (time.monotonic(), val)
    return val


def get(name: str, database_url: str | None = None) -> str | None:
    if name not in NAMES:
        raise KeyError(name)
    env = os.environ.get(NAMES[name])
    if env:
        return env
    url = database_url or _database_url
    if not url:
        return None

    def load():
        try:
            with db.tx(url) as conn:
                row = conn.execute("SELECT ciphertext FROM flightbridge.integration_secret WHERE name=%s", (name,)).fetchone()
        except Exception:
            return None          # table missing (migration 004 not applied) => behave as "not set"
        if not row:
            return None
        try:
            return decrypt(row[0])
        except SecretUnreadable:
            return None
    return _cached(("secret", url, name), load)


def settings(database_url: str | None = None) -> dict:
    url = database_url or _database_url
    out = dict(DEFAULT_SETTINGS)
    if not url:
        return out

    def load():
        try:
            with db.tx(url) as conn:
                row = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='integrations'").fetchone()
            return row[0] if row and isinstance(row[0], dict) else {}
        except Exception:
            return {}
    out.update({k: v for k, v in _cached(("settings", url), load).items() if k in DEFAULT_SETTINGS})
    return out


def save(conn, name: str, value: str, employee_id=None) -> str:
    """Encrypt and store one key in an open transaction; returns last4. Plaintext is never logged."""
    if name not in NAMES:
        raise KeyError(name)
    value = (value or "").strip()
    if not value or len(value) > 4000:
        raise ValueError("enter a key (up to 4000 characters)")
    l4 = last4(value)
    conn.execute(
        """INSERT INTO flightbridge.integration_secret (name, ciphertext, last4, updated_at, updated_by)
           VALUES (%s,%s,%s,now(),%s) ON CONFLICT (name) DO UPDATE
           SET ciphertext=EXCLUDED.ciphertext, last4=EXCLUDED.last4, updated_at=now(), updated_by=EXCLUDED.updated_by,
               last_test_at=NULL, last_test_ok=NULL, last_test_message=NULL""",
        (name, encrypt(value), l4, employee_id))
    _cache.clear()
    return l4


def dumps_settings(s: dict) -> str:
    return json.dumps({k: s[k] for k in DEFAULT_SETTINGS if k in s})
