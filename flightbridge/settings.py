"""Runtime settings. Safe defaults: DRY_RUN=true, OUTBOUND_ENABLED=false.

Parsing is deliberately asymmetric: only the exact string "false" disables
DRY_RUN and only the exact string "true" enables OUTBOUND_ENABLED. Typos keep
the system safe.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .compliance.content import SenderProfile

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_url: str
    dry_run: bool = True
    outbound_enabled: bool = False
    hostinger_base_url: str = "https://api.mail.hostinger.com"
    hostinger_token: str | None = None
    hostinger_mailbox_id: str | None = None
    hostinger_sent_folder: str | None = None   # None => resolve via specialUse \\Sent
    sender_display_name: str = "We One Aviation"
    inbound_source: str | None = None          # "hostinger" | "imap" | None (no inbox polling)
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None
    crawler_user_agent: str = "FlightBridgeBot/0.1 (+https://weoneaviation.in/contact)"

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        return cls(
            database_url=env.get("FLIGHTBRIDGE_DATABASE_URL") or env.get("DATABASE_URL", ""),
            dry_run=env.get("DRY_RUN", "true").strip() != "false",
            outbound_enabled=env.get("OUTBOUND_ENABLED", "false").strip() == "true",
            hostinger_token=env.get("HOSTINGER_MAIL_API_TOKEN") or None,
            hostinger_mailbox_id=env.get("HOSTINGER_MAILBOX_RESOURCE_ID") or None,
            hostinger_sent_folder=env.get("HOSTINGER_SENT_FOLDER") or None,
            inbound_source=(env.get("INBOUND_SOURCE") or "").strip().lower() or None,
            imap_host=env.get("IMAP_HOST") or None,
            imap_port=int(env.get("IMAP_PORT") or 993),
            imap_user=env.get("IMAP_USER") or None,
            imap_password=env.get("IMAP_PASSWORD") or None,
        )

    @property
    def may_transmit(self) -> bool:
        """Env-level permission only; the DB kill switch is checked separately at send time."""
        return (not self.dry_run) and self.outbound_enabled


def load_sender_profile(path: Path = ROOT / "config" / "sender_profile.json") -> SenderProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return SenderProfile(**{k: data.get(k) for k in SenderProfile.__dataclass_fields__})
