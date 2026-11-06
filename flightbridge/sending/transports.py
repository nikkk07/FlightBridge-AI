"""Mail transports. Import is restricted to flightbridge.sending.service (enforced by
tests/test_single_send_path.py) and construction requires the service's capability token.

Hostinger Mail API facts used here (hostinger-mail-api SDK v1.18.2, generated from the API spec):
  POST /api/v1/mailboxes/{mailboxResourceId}/send  -> 204 No Content (no message id returned)
  body: to[], subject, text, displayName, inReplyTo{uid, folder}
  POST /api/v1/mailboxes/{id}/folders/{folder}/messages/search  body: {"body": "..."} substring match
Live read-only probe 2026-10-31 (info@weoneaviation.com): /me, /folders, /messages return 200; message fields have no
thread id/references; Sent folder path is 'INBOX.Sent'. send/search have NOT been exercised live.

SMTP + IMAP mailbox (HIOX India cPanel mail, partnership@weoneaviation.in; server checked 2026-11-05: MX
host2.dnsinweb.com, IMAP 993 TLS, SMTP 465 TLS, certificate valid for host2.dnsinweb.com). The message is sent over SMTP
(465, TLS), then a copy is saved in the Sent folder over IMAP so the send can be confirmed by its Ref: token later.
"""
from __future__ import annotations

import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass
from urllib.parse import quote

import requests


class TransportError(Exception):
    """Base. `definite` = the provider certainly did not accept the message."""
    definite = False


class DefiniteRejection(TransportError):
    definite = True


class NotSent(TransportError):
    """The mail server could not be reached at all (e.g. a host that blocks SMTP ports): certainly nothing was sent,
    so the message stays authorised and another runner sends it."""
    definite = True


@dataclass(frozen=True)
class SendRequest:
    to: str
    subject: str
    text: str
    display_name: str
    reply_uid: int | None = None
    reply_folder: str | None = None
    in_reply_to: str | None = None        # RFC Message-ID of the school's email (SMTP mailboxes)
    references: tuple = ()                # earlier Message-IDs in the conversation (follow-ups thread under the first email)
    unsubscribe_url: str | None = None    # one-click unsubscribe (RFC 8058) - SMTP mailboxes only


class HostingerMailApiTransport:
    def __init__(self, *, base_url: str, token: str, mailbox_id: str, capability: object, timeout: float = 20.0):
        from .service import _SEND_CAPABILITY
        if capability is not _SEND_CAPABILITY:
            raise PermissionError("transports may only be constructed by SendingService")
        if not token or not mailbox_id:
            raise ValueError("Hostinger token and mailbox id are required")
        self._base = base_url.rstrip("/")
        self._mbx = quote(mailbox_id, safe="")
        self._timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json",
                                "User-Agent": "hostinger_mail_api-python/1.18.2"})  # default UA is Cloudflare-blocked (observed 2026-10-31)

    def send(self, req: SendRequest) -> None:
        payload = {"to": [req.to], "subject": req.subject, "text": req.text, "displayName": req.display_name}
        if req.reply_uid is not None:
            payload["inReplyTo"] = {"uid": req.reply_uid, "folder": req.reply_folder}
        try:
            r = self._s.post(f"{self._base}/api/v1/mailboxes/{self._mbx}/send", json=payload, timeout=self._timeout)
        except requests.RequestException as exc:     # timeout / connection reset: outcome unknown
            raise TransportError(f"network:{type(exc).__name__}") from exc
        if r.status_code == 204:
            return
        if r.status_code in (400, 401, 403, 404, 422):
            raise DefiniteRejection(f"http_{r.status_code}")
        raise TransportError(f"http_{r.status_code}")  # 429/5xx: may or may not have been accepted

    def special_folder(self, special_use: str = "\\Sent") -> str:
        """Resolve a folder path by IMAP special-use flag. Live probe 2026-10-31: Sent = 'INBOX.Sent' (not 'Sent')."""
        r = self._s.get(f"{self._base}/api/v1/mailboxes/{self._mbx}/folders", timeout=self._timeout)
        r.raise_for_status()
        for f in r.json().get("data") or []:
            if f.get("specialUse") == special_use:
                return f["path"]
        raise LookupError(f"no folder with specialUse {special_use}")

    def find_sent_by_ref(self, ref_token: str, folder: str | None = None) -> bool:
        folder = folder or self.special_folder("\\Sent")
        r = self._s.post(f"{self._base}/api/v1/mailboxes/{self._mbx}/folders/{quote(folder, safe='')}/messages/search",
                         json={"body": ref_token}, params={"per_page": 5}, timeout=self._timeout)
        r.raise_for_status()
        return bool(r.json().get("data"))


class SmtpImapTransport:
    def __init__(self, *, host: str, user: str, password: str, capability: object, smtp_port: int = 465,
                 imap_port: int = 993, timeout: float = 30.0):
        from .service import _SEND_CAPABILITY
        if capability is not _SEND_CAPABILITY:
            raise PermissionError("transports may only be constructed by SendingService")
        if not host or not user or not password:
            raise ValueError("mail server, address and password are required")
        self.host, self.user, self._password = host, user, password
        self.smtp_port, self.imap_port, self._timeout = smtp_port, imap_port, timeout

    def build(self, req: SendRequest):
        from email.message import EmailMessage
        from email.utils import formataddr, formatdate, make_msgid
        m = EmailMessage()
        m["From"] = formataddr((req.display_name, self.user))
        m["To"] = req.to
        m["Subject"] = req.subject
        m["Date"] = formatdate(localtime=False)
        m["Message-ID"] = make_msgid(domain=self.user.split("@")[-1])
        if req.in_reply_to:
            m["In-Reply-To"] = req.in_reply_to
            m["References"] = " ".join(dict.fromkeys([*req.references, req.in_reply_to]))
        # Unsubscribe header: mail apps show their own "Unsubscribe" button, which is far better for the sender's
        # reputation than a "Report spam" click. mailto: always works (a reply is read by the inbox reader).
        unsub = [f"<mailto:{self.user}?subject=unsubscribe>"]
        if req.unsubscribe_url:
            unsub.insert(0, f"<{req.unsubscribe_url}>")
            m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        m["List-Unsubscribe"] = ", ".join(unsub)
        m.set_content(req.text)
        return m

    def send(self, req: SendRequest) -> str:
        """-> the Message-ID it went out with (stored, so follow-ups and replies thread with it)."""
        msg = self.build(req)
        try:
            smtp = smtplib.SMTP_SSL(self.host, self.smtp_port, timeout=self._timeout, context=ssl.create_default_context())
        except (OSError, smtplib.SMTPException) as exc:          # never connected: nothing was sent
            raise NotSent(f"connect:{type(exc).__name__}") from exc
        try:
            try:
                smtp.login(self.user, self._password)
            except smtplib.SMTPAuthenticationError as exc:
                raise DefiniteRejection("smtp_login_refused") from exc
            try:
                refused = smtp.send_message(msg)
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
                raise DefiniteRejection(type(exc).__name__) from exc
            except smtplib.SMTPDataError as exc:
                if 500 <= exc.smtp_code < 600:
                    raise DefiniteRejection(f"smtp_{exc.smtp_code}") from exc
                raise TransportError(f"smtp_{exc.smtp_code}") from exc
            except (OSError, smtplib.SMTPException) as exc:     # cut off mid-send: outcome unknown
                raise TransportError(f"network:{type(exc).__name__}") from exc
            if refused:
                raise DefiniteRejection("recipient_refused")
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        try:
            self._save_copy(msg)          # best effort: the email has gone either way
        except Exception:
            pass
        return msg["Message-ID"]

    def _imap(self):
        m = imaplib.IMAP4_SSL(self.host, self.imap_port, ssl_context=ssl.create_default_context(), timeout=self._timeout)
        m.login(self.user, self._password)
        return m

    @staticmethod
    def _sent_folder(m) -> str:
        typ, rows = m.list()
        for raw in rows or []:
            line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            if "\\Sent" in line:
                name = re.findall(r'"([^"]+)"\s*$', line) or [line.rsplit(" ", 1)[-1]]
                return name[0]
        return "INBOX.Sent"           # cPanel / Dovecot default

    def _save_copy(self, msg) -> None:
        with self._imap() as m:
            m.append(self._sent_folder(m), "\\Seen", None, msg.as_bytes())

    def find_sent_by_ref(self, ref_token: str, folder: str | None = None) -> bool:
        with self._imap() as m:
            typ, _ = m.select(folder or self._sent_folder(m), readonly=True)
            if typ != "OK":
                return False
            typ, data = m.search(None, "BODY", f'"{ref_token}"')
            return bool(typ == "OK" and data and data[0].split())
