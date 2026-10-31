"""Mail transports. Import is restricted to flightbridge.sending.service (enforced by
tests/test_single_send_path.py) and construction requires the service's capability token.

Hostinger Mail API facts used here (hostinger-mail-api SDK v1.18.2, generated from the API spec):
  POST /api/v1/mailboxes/{mailboxResourceId}/send  -> 204 No Content (no message id returned)
  body: to[], subject, text, displayName, inReplyTo{uid, folder}
  POST /api/v1/mailboxes/{id}/folders/{folder}/messages/search  body: {"body": "..."} substring match
Live behaviour has NOT been exercised (no token available); run scripts/probe_hostinger.py first.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import requests


class TransportError(Exception):
    """Base. `definite` = the provider certainly did not accept the message."""
    definite = False


class DefiniteRejection(TransportError):
    definite = True


@dataclass(frozen=True)
class SendRequest:
    to: str
    subject: str
    text: str
    display_name: str
    reply_uid: int | None = None
    reply_folder: str | None = None


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
        self._s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

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

    def find_sent_by_ref(self, ref_token: str, folder: str) -> bool:
        r = self._s.post(f"{self._base}/api/v1/mailboxes/{self._mbx}/folders/{quote(folder, safe='')}/messages/search",
                         json={"body": ref_token}, params={"per_page": 5}, timeout=self._timeout)
        r.raise_for_status()
        return bool(r.json().get("data"))
