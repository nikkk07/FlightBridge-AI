"""Live checks for API keys entered in the app ("Test" buttons). Each check is free or the cheapest call available:
  Google Places  - Text Search with field mask places.id only (the IDs-only SKU)
  Groq           - GET /openai/v1/models
  Gemini         - GET /v1beta/models
  Hostinger      - GET /api/v1/me, and GET /api/v1/mailboxes/{id}/folders for the mailbox id
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


def check(name: str, value: str, mailbox_token: str | None = None) -> tuple[bool, str]:
    import requests
    try:
        if name == "google_places_api_key":
            from .research.places import PlacesError, search
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
