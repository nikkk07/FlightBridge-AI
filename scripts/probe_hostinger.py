"""READ-ONLY probe of the Hostinger Mail API. Sends nothing, changes nothing.

Purpose: replace documentation assumptions with observed behaviour before Phase 5.
  * Does any message resource carry a thread identifier (thread_id / threadId / references)?
  * What does the Sent folder look like (name), so send_unknown reconciliation can search it?
Usage (token from hPanel > Agentic Mail > API access; never commit it):
  HOSTINGER_MAIL_API_TOKEN=... python3 scripts/probe_hostinger.py
Webhook thread_id test (manual, no code here): create a webhook pointing at a request-bin you control,
send yourself one test email, and inspect the delivered JSON for a thread field. Record the result in
docs/PHASE_2_ARCHITECTURE.md (Hostinger threading section).
"""
import json
import os
import sys

import requests

BASE = os.environ.get("HOSTINGER_BASE_URL", "https://api.mail.hostinger.com")
TOKEN = os.environ.get("HOSTINGER_MAIL_API_TOKEN", "")
if not TOKEN.strip():
    sys.exit("HOSTINGER_MAIL_API_TOKEN is empty")
# Safe diagnostics only: never print the token itself.
print(f"token check: length={len(TOKEN)}, has_whitespace={any(c.isspace() for c in TOKEN)}, "
      f"looks_like_command={'python' in TOKEN or 'scripts/' in TOKEN}")
if any(c.isspace() for c in TOKEN):
    sys.exit("Token contains spaces/newlines - it was probably captured from the wrong pasted line. Re-enter it alone.")
S = requests.Session()
# Same User-Agent as Hostinger's official SDK (api_client.py v1.18.2); the default python-requests UA is blocked by Cloudflare.
S.headers.update({"Authorization": f"Bearer {TOKEN}", "Accept": "application/json",
                  "User-Agent": "hostinger_mail_api-python/1.18.2"})


def get(path, **params):
    r = S.get(BASE + path, params=params, timeout=20)
    print(f"GET {path} -> {r.status_code}")
    if r.status_code >= 400:
        body = r.text.replace(TOKEN, "<redacted>")
        if "cloudflare" in body.lower():
            import re
            ray = re.search(r"Ray ID: <strong[^>]*>([0-9a-f]+)", body)
            sys.exit(f"Blocked by Cloudflare before reaching Hostinger (Ray ID: {ray.group(1) if ray else 'n/a'}).")
        sys.exit(f"Hostinger error body: {body[:500]}")
    return r.json()


me = get("/api/v1/me")
mailboxes = me.get("data", {}).get("mailboxes", [])
print("mailboxes:", [m.get("address") for m in mailboxes])
for m in mailboxes[:1]:
    rid = m["resourceId"]
    folders = get(f"/api/v1/mailboxes/{rid}/folders")
    print("folders:", json.dumps(folders.get("data"), indent=1)[:1500])
    msgs = get(f"/api/v1/mailboxes/{rid}/folders/INBOX/messages", per_page=3)
    items = msgs.get("data") or []
    keys = sorted({k for it in items for k in it.keys()})
    print("message fields observed:", keys)
    print("thread-like fields:", [k for k in keys if "thread" in k.lower() or "reference" in k.lower()] or "NONE")
