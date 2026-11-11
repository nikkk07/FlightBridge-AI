"""Live checks for API keys entered in the app ("Test" buttons). Each check is free or the cheapest call available:
  Google Places  - Text Search with field mask places.id only (the IDs-only SKU)
  Groq           - GET /openai/v1/models
  Gemini         - GET /v1beta/models
  Hostinger      - GET /api/v1/me, and GET /api/v1/mailboxes/{id}/folders for the mailbox id
  HIOX mailbox   - IMAP login (993, TLS) and SMTP login (465, TLS); nothing is sent
Returns (ok, plain-English message). Never raises for network/API errors.
"""
from __future__ import annotations

HOSTINGER = "https://api.mail.hostinger.com"
HOSTINGER_UA = "hostinger_mail_api-python/1.18.2"   # default python-requests UA is blocked by Cloudflare


def _explain_google(msg: str) -> str:
    if "has not been used in project" in msg or "SERVICE_DISABLED" in msg or "is disabled" in msg:
        return "Key works, but Places API (New) is not enabled in its Google Cloud project. Enable it, then test again."
    if "API key not valid" in msg or "API_KEY_INVALID" in msg:
        return "Google says this key is not valid. Copy it again from Google Cloud > Credentials."
    if "referer" in msg.lower() or "restricted" in msg.lower():
        return "The key is restricted (website/app restriction). Allow server use or set restriction to Places API (New) only."
    return msg[:300]


def check_mailbox(host: str, user: str, password: str) -> tuple[bool, str]:
    """Log in to IMAP and SMTP (no message is sent)."""
    import imaplib
    import smtplib
    import ssl
    try:
        with imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=20) as m:
            m.login(user, password)
    except imaplib.IMAP4.error:
        return False, "The mail server refused the address or password (IMAP)."
    except OSError as exc:
        return False, f"Could not reach {host}:993 ({type(exc).__name__})."
    try:
        with smtplib.SMTP_SSL(host, 465, timeout=20, context=ssl.create_default_context()) as s:
            s.login(user, password)
    except smtplib.SMTPAuthenticationError:
        return False, "Replies can be read, but the server refused the login for sending (SMTP)."
    except (OSError, smtplib.SMTPException) as exc:
        return True, f"Connected for reading replies. Sending could not be checked from here ({type(exc).__name__}); it runs on the Mac Mini."
    return True, f"Connected: {user} can send and read replies."


def check(name: str, value: str, mailbox_token: str | None = None, mail: dict | None = None) -> tuple[bool, str]:
    import requests
    try:
        if name == "hiox_mail_password":
            mail = mail or {}
            return check_mailbox(mail.get("hiox_mail_host") or "host2.dnsinweb.com",
                                 mail.get("hiox_mail_user") or "partnership@weoneaviation.in", value)
        if name == "google_places_api_key":
            from .research import quota
            from .research.places import PlacesError, search
            if quota._installed.get("url"):
                quota.clear_pause(quota._installed["url"])   # a person pressing Test always gets a real answer from Google
            try:
                search("flight training school", None, "GB", api_key=value, field_mask="places.id")
                return True, "Connected. Website search can run."
            except PlacesError as exc:
                return False, _explain_google(str(exc))
        if name == "groq_api_key":
            r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {value}"}, timeout=15)
            return (True, "Connected to Groq (free tier).") if r.ok else (False, f"Groq refused the key (HTTP {r.status_code}).")
        if name == "gemini_api_key":
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": value}, timeout=15)
            return (True, "Connected to Gemini (free tier).") if r.ok else (False, f"Gemini refused the key (HTTP {r.status_code}).")
        if name == "tavily_api_key":     # one basic search = 1 of the 1,000 free monthly credits
            r = requests.post("https://api.tavily.com/search", timeout=20, headers={"Authorization": f"Bearer {value}"},
                              json={"query": "flight school", "search_depth": "basic", "max_results": 1})
            return (True, "Connected to Tavily (free 1,000 searches a month).") if r.ok else \
                (False, f"Tavily refused the key (HTTP {r.status_code}).")
        if name == "hostinger_mail_api_token":
            r = requests.get(f"{HOSTINGER}/api/v1/me", timeout=15,
                             headers={"Authorization": f"Bearer {value}", "Accept": "application/json", "User-Agent": HOSTINGER_UA})
            return (True, "Connected to Hostinger Mail.") if r.ok else (False, f"Hostinger refused the token (HTTP {r.status_code}).")
        if name == "hostinger_mailbox_resource_id":
            if not mailbox_token:
                return False, "Save the Hostinger API token first, then test the mailbox."
            r = requests.get(f"{HOSTINGER}/api/v1/mailboxes/{value}/folders", timeout=15,
                             headers={"Authorization": f"Bearer {mailbox_token}", "Accept": "application/json", "User-Agent": HOSTINGER_UA})
            return (True, "Mailbox found. Replies can be read.") if r.ok else (False, f"Mailbox not found for this token (HTTP {r.status_code}).")
        return False, "Unknown key."
    except requests.RequestException as exc:
        return False, f"Could not reach the service ({type(exc).__name__}). Try again."
