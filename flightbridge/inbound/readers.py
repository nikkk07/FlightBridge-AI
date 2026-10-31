"""Read-only inbox readers. They never send, delete, move or flag messages.

HostingerApiReader: GET /messages (newest first, stops at the cursor) + GET /messages/{uid}/source (raw RFC 822).
  The summary resource has no References header (live probe 2026-10-31), so the raw source is parsed instead.
ImapReader: for a mailbox not hosted at Hostinger (today info@weoneaviation.com's MX points elsewhere).
  Opens the folder read-only (EXAMINE) and fetches BODY.PEEK[] so nothing is marked as read.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import quote

import requests

ID_RE = re.compile(r"<[^<>\s]+>")


@dataclass
class InboundMessage:
    mailbox: str               # "hostinger:<resourceId>" or "imap:<user>@<host>"
    folder: str
    uid: int
    message_id: str | None
    in_reply_to: str | None
    references: list = field(default_factory=list)
    from_address: str | None = None
    to: list = field(default_factory=list)
    subject: str = ""
    date: datetime | None = None
    text: str = ""
    headers: dict = field(default_factory=dict)   # lower-cased subset used by detectors


KEEP_HEADERS = ("auto-submitted", "x-autoreply", "x-autorespond", "precedence", "content-type",
                "x-failed-recipients", "list-unsubscribe", "return-path")


def _text_of(msg) -> str:
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    parts.append(part.get_content())
                except Exception:
                    parts.append(part.get_payload(decode=True).decode("utf-8", "replace") if part.get_payload(decode=True) else "")
            elif part.get_content_type() == "message/delivery-status":
                parts.append(str(part.get_payload()))
        if parts:
            return "\n".join(parts)
        for part in msg.walk():                      # html-only fallback: strip tags
            if part.get_content_type() == "text/html":
                return re.sub(r"<[^>]+>", " ", part.get_content())
        return ""
    try:
        body = msg.get_content()
    except Exception:
        body = (msg.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return re.sub(r"<[^>]+>", " ", body) if msg.get_content_type() == "text/html" else body


def parse_rfc822(raw: bytes, mailbox: str, folder: str, uid: int) -> InboundMessage:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    froms = getaddresses([str(msg.get("From", ""))])
    tos = getaddresses([str(msg.get("To", "")), str(msg.get("Cc", ""))])
    try:
        date = parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
        if date and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        date = None
    irt = ID_RE.findall(str(msg.get("In-Reply-To", "")))
    return InboundMessage(
        mailbox=mailbox, folder=folder, uid=int(uid),
        message_id=(ID_RE.findall(str(msg.get("Message-ID", ""))) or [None])[0],
        in_reply_to=irt[0] if irt else None,
        references=ID_RE.findall(str(msg.get("References", ""))),
        from_address=froms[0][1].strip().lower() if froms and froms[0][1] else None,
        to=[a.strip().lower() for _, a in tos if a],
        subject=str(msg.get("Subject", "") or ""),
        date=date,
        text=_text_of(msg)[:200_000],
        headers={h: str(msg.get(h)) for h in KEEP_HEADERS if msg.get(h) is not None},
    )


class HostingerApiReader:
    def __init__(self, base_url: str, token: str, mailbox_id: str, timeout: float = 20.0):
        self.base, self.mbx, self.timeout = base_url.rstrip("/"), mailbox_id, timeout
        self.mailbox = f"hostinger:{mailbox_id}"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json",
                               "User-Agent": "hostinger_mail_api-python/1.18.2"})

    def _get(self, path: str, **params):
        r = self.s.get(f"{self.base}/api/v1/mailboxes/{quote(self.mbx, safe='')}{path}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r

    def fetch_since(self, folder: str, last_uid: int, limit: int = 100) -> list[InboundMessage]:
        uids, page = [], 1
        while len(uids) < limit:
            data = self._get(f"/folders/{quote(folder, safe='')}/messages", sort="-uid", per_page=100, page=page).json()
            items = data.get("data") or []
            new = [int(i["uid"]) for i in items if int(i["uid"]) > last_uid]
            uids.extend(new)
            if len(new) < len(items) or len(items) < 100:
                break
            page += 1
        out = []
        for uid in sorted(uids)[:limit]:
            raw = self._get(f"/folders/{quote(folder, safe='')}/messages/{uid}/source").content
            out.append(parse_rfc822(raw, self.mailbox, folder, uid))
        return out


class ImapReader:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.mailbox = f"imap:{user}@{host}"

    def fetch_since(self, folder: str, last_uid: int, limit: int = 100) -> list[InboundMessage]:
        out = []
        with imaplib.IMAP4_SSL(self.host, self.port) as m:
            m.login(self.user, self.password)
            typ, _ = m.select(folder, readonly=True)
            if typ != "OK":
                raise RuntimeError(f"cannot open folder {folder}")
            typ, data = m.uid("SEARCH", None, f"UID {last_uid + 1}:*")
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last_uid)[:limit]
            for uid in uids:
                typ, parts = m.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    out.append(parse_rfc822(raw, self.mailbox, folder, uid))
        return out
