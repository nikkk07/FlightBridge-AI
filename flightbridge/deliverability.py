"""Pre-flight checks before Email Auto-Pilot may send: will the mail be trusted, and is everything switched on?

What mailbox providers check for every sender (Gmail's sender guidelines, also Microsoft and Yahoo): SPF and/or DKIM
on the sending domain, one valid DMARC record, reverse DNS (PTR) on the sending server, TLS, and a low spam-complaint
rate; plus an unsubscribe option. Each check returns pass / warn / fail with the fix in plain words. Any 'fail' keeps
Auto-Pilot from sending. DNS is read over DNS-over-HTTPS (dns.google), blocklists through this computer's resolver.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import datetime, timezone

import requests

from . import db

DKIM_SELECTORS = ("default", "hostingermail-a", "hostingermail-b", "hostingermail-c", "hostingermail1", "dkim", "mail",
                  "x", "k1", "selector1", "selector2", "google", "s1", "s2")
SPAM_WORDS = ("free", "guarantee", "guaranteed", "urgent", "act now", "limited time", "risk-free", "risk free",
              "winner", "cash", "100%", "click here", "buy now", "earn money", "no obligation", "special promotion",
              "once in a lifetime", "exclusive deal", "congratulations", "$$$", "best price", "lowest price")
SHORTENERS = ("bit.ly", "tinyurl", "goo.gl", "t.co/", "ow.ly", "is.gd", "buff.ly", "rebrand.ly", "cutt.ly")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


# ---------------------------------------------------------------------------------------------------- DNS
class DohResolver:
    """Public DNS over HTTPS (works the same on the Mac and on the CRM server)."""
    URL = "https://dns.google/resolve"
    TYPES = {"A": 1, "MX": 15, "TXT": 16, "PTR": 12, "AAAA": 28}

    def __init__(self, session=None, timeout: float = 8.0):
        self.s, self.timeout = session or requests.Session(), timeout

    def _q(self, name: str, rtype: str) -> list[str]:
        r = self.s.get(self.URL, params={"name": name, "type": rtype}, timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        if j.get("Status") not in (0, 3):            # 3 = NXDOMAIN: simply no record
            raise LookupError(f"DNS {rtype} {name}: status {j.get('Status')}")
        return [a["data"] for a in j.get("Answer") or [] if a.get("type") == self.TYPES[rtype]]

    def txt(self, name: str) -> list[str]:
        out = []
        for d in self._q(name, "TXT"):
            parts = re.findall(r'"((?:[^"\\]|\\.)*)"', d)
            out.append("".join(parts) if parts else d)
        return out

    def a(self, name: str) -> list[str]:
        return [x for x in self._q(name, "A") if re.fullmatch(r"[\d.]+", x)]

    def aaaa(self, name: str) -> list[str]:
        return self._q(name, "AAAA")

    def mx(self, name: str) -> list[str]:
        return [d.split()[-1].rstrip(".") for d in self._q(name, "MX")]

    def ptr(self, ip: str) -> list[str]:
        return [d.rstrip(".") for d in self._q(ipaddress.ip_address(ip).reverse_pointer, "PTR")]

    @staticmethod
    def blocklist(qname: str) -> str:
        """'listed' | 'clean' | 'unknown'. Spamhaus refuses public resolvers (127.255.255.x): that is 'unknown'."""
        try:
            ip = socket.gethostbyname(qname)
        except socket.gaierror:
            return "clean"
        except OSError:
            return "unknown"
        return "unknown" if ip.startswith("127.255.255.") else ("listed" if ip.startswith("127.") else "unknown")


def spf_allows(domain: str, ip: str, dns, budget: list | None = None) -> bool | None:
    """Does the SPF record of `domain` authorise `ip`? None when it can't be told (no record, too many look-ups)."""
    budget = budget if budget is not None else [10]
    recs = [t for t in dns.txt(domain) if t.lower().startswith("v=spf1")]
    if len(recs) != 1:
        return None
    addr = ipaddress.ip_address(ip)
    redirect = None
    for term in recs[0].split()[1:]:
        q = term[0] if term[0] in "+-~?" else "+"
        mech = term[1:] if term[0] in "+-~?" else term
        name, _, arg = mech.partition(":")
        name = name.lower()
        if "=" in name:
            if name.startswith("redirect="):
                redirect = mech.split("=", 1)[1]
            continue
        hit = False
        if name in ("ip4", "ip6"):
            try:
                hit = addr in ipaddress.ip_network(arg, strict=False)
            except ValueError:
                hit = False
        elif name.split("/")[0] in ("a", "mx"):
            base, _, cidr = (arg or domain).partition("/")
            budget[0] -= 1
            if budget[0] < 0:
                return None
            hosts = [base] if name.startswith("a") else dns.mx(base)
            for h in hosts:
                for x in (dns.a(h) if addr.version == 4 else dns.aaaa(h)):
                    try:
                        net = ipaddress.ip_network(f"{x}/{cidr or (32 if addr.version == 4 else 128)}", strict=False)
                        hit = hit or addr in net
                    except ValueError:
                        pass
        elif name == "include":
            budget[0] -= 1
            if budget[0] < 0:
                return None
            sub = spf_allows(arg, ip, dns, budget)
            hit = bool(sub)
        elif name == "all":
            return q in "+"
        if hit:
            return q in "+"
    if redirect:
        budget[0] -= 1
        return spf_allows(redirect, ip, dns, budget) if budget[0] >= 0 else None
    return False


# ---------------------------------------------------------------------------------------------------- checks
def _check(cid: str, label: str, status: str, detail: str, fix: str | None = None) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail, "fix": fix}


def template_issues(subject: str, body: str) -> list[str]:
    out = []
    text = f"{subject}\n{body}".lower()
    words = sorted({w for w in SPAM_WORDS if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text)})
    if words:
        out.append("spam-trigger words: " + ", ".join(words))
    if subject and sum(c.isupper() for c in subject) > 0.5 * max(1, sum(c.isalpha() for c in subject)):
        out.append("subject is mostly CAPITALS")
    if "!" in subject:
        out.append("exclamation mark in the subject")
    if len(subject) > 70:
        out.append("subject longer than 70 characters")
    links = URL_RE.findall(body or "")
    if len(links) > 1:
        out.append(f"{len(links)} links (keep it to one)")
    if any(s in (body or "").lower() for s in SHORTENERS):
        out.append("link shortener (looks like spam)")
    return out


def run(database_url: str, may_transmit: bool, dns=None) -> dict:
    """Every check, stored in system_setting 'send_autopilot_state'.preflight. Returns that preflight dict."""
    from . import secrets
    dns = dns or DohResolver()
    cfg = secrets.settings(database_url)
    checks: list[dict] = []
    with db.tx(database_url) as conn:
        ks = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='outbound_kill_switch'").fetchone()
        kill_engaged = ks is None or ks[0] is not False
        tests = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT name, last_test_ok, last_test_message FROM flightbridge.integration_secret").fetchall()}
        tpls = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT kind, subject, body FROM flightbridge.outreach_template WHERE status='approved'").fetchall()}
        fdays = conn.execute("SELECT value FROM flightbridge.system_setting WHERE key='followup_schedule_days'").fetchone()
    fdays = fdays[0] if fdays and isinstance(fdays[0], list) else [4, 9, 16]

    checks.append(_check("server", "Sending switched on for this engine", "pass" if may_transmit else "fail",
                         "DRY_RUN=false and OUTBOUND_ENABLED=true" if may_transmit else "DRY_RUN is on (or OUTBOUND_ENABLED is off), so nothing can be sent",
                         None if may_transmit else "In ~/FlightBridgeEngine/.env on the Mac set DRY_RUN=false and OUTBOUND_ENABLED=true, then run install.sh"))
    checks.append(_check("kill_switch", "Kill switch released", "fail" if kill_engaged else "pass",
                         "Sending is blocked by the kill switch" if kill_engaged else "Released",
                         "Press 'Sending blocked (kill switch on)' at the top of FlightBridge AI" if kill_engaged else None))

    # mailbox ------------------------------------------------------------------------------------------------
    hiox = cfg.get("send_from") == "hiox"
    user = cfg.get("hiox_mail_user") or ""
    host = cfg.get("hiox_mail_host") or ""
    if hiox:
        ok, msg = tests.get("hiox_mail_password", (None, None))
        checks.append(_check("mailbox", f"Mailbox {user} connected", "pass" if ok else "fail",
                             msg or ("Login works" if ok else "Not tested yet"),
                             None if ok else "Settings > Email: enter the mailbox password and press Save & test"))
        domain = user.split("@")[-1].lower()
    else:
        ok, msg = tests.get("hostinger_mail_api_token", (None, None))
        checks.append(_check("mailbox", "Hostinger mailbox connected", "pass" if ok else "fail",
                             msg or ("Connected" if ok else "Not tested yet"),
                             None if ok else "Settings > Email: save the Hostinger token and press Test"))
        mid = secrets.get("hostinger_mailbox_resource_id", database_url) or ""
        domain = mid.split("@")[-1].lower() if "@" in mid else ""
        user = mid if "@" in mid else user
    if not domain:
        checks.append(_check("domain", "Sending domain", "fail", "No sending address is set",
                             "Settings > Email: choose the mailbox that sends"))
        return _store(database_url, checks)

    # DNS ----------------------------------------------------------------------------------------------------
    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as exc:               # DNS service unreachable: say so, don't guess
            return exc

    txt = safe(dns.txt, domain)
    spf = [t for t in txt if t.lower().startswith("v=spf1")] if isinstance(txt, list) else txt
    server_ips = safe(dns.a, host) if hiox and host else []
    if isinstance(spf, Exception):
        checks.append(_check("spf", "SPF record", "warn", f"Couldn't read DNS: {spf}"))
    elif len(spf) == 0:
        checks.append(_check("spf", "SPF record", "fail", f"{domain} has no SPF record",
                             f"Add a TXT record on {domain}: v=spf1 +a +mx include:<your mail server> ~all (HIOX / Hostinger support give the exact value)"))
    elif len(spf) > 1:
        checks.append(_check("spf", "SPF record", "fail", f"{domain} has {len(spf)} SPF records (only one is allowed, so SPF fails)",
                             "Merge them into a single TXT record that starts with v=spf1"))
    elif hiox and isinstance(server_ips, list) and server_ips:
        verdict = safe(spf_allows, domain, server_ips[0], dns)
        if verdict is True:
            checks.append(_check("spf", "SPF record", "pass", f"Authorises the mail server {host} ({server_ips[0]})"))
        elif verdict is False:
            checks.append(_check("spf", "SPF record", "fail", f"The SPF record does not include the mail server {host} ({server_ips[0]})",
                                 f"Add ip4:{server_ips[0]} (or include the server) to the SPF record of {domain}"))
        else:
            checks.append(_check("spf", "SPF record", "warn", "Record found; couldn't confirm it covers the mail server"))
    else:
        checks.append(_check("spf", "SPF record", "pass", spf[0][:120]))

    found = []
    for sel in DKIM_SELECTORS:
        t = safe(dns.txt, f"{sel}._domainkey.{domain}")
        if isinstance(t, list) and any(re.search(r"(^|;)\s*p=[A-Za-z0-9+/]", x) for x in t):
            found.append(sel)
    checks.append(_check("dkim", "DKIM signature key", "pass" if found else "fail",
                         f"Key published (selector: {', '.join(found)})" if found else f"No DKIM key found for {domain}",
                         None if found else "Turn on DKIM for the domain in the mail host's panel (cPanel > Email Deliverability) and add the record it shows"))

    dm = safe(dns.txt, f"_dmarc.{domain}")
    dm = [t for t in dm if t.lower().replace(" ", "").startswith("v=dmarc1")] if isinstance(dm, list) else dm
    if isinstance(dm, Exception):
        checks.append(_check("dmarc", "DMARC record", "warn", f"Couldn't read DNS: {dm}"))
    elif len(dm) == 1:
        pol = re.search(r"\bp\s*=\s*(\w+)", dm[0], re.I)
        checks.append(_check("dmarc", "DMARC record", "pass", f"One record, policy {pol.group(1) if pol else '?'}"))
    elif not dm:
        checks.append(_check("dmarc", "DMARC record", "fail", f"_dmarc.{domain} has no DMARC record",
                             f"Add a TXT record _dmarc.{domain}: v=DMARC1; p=none; rua=mailto:{user or 'you@' + domain}"))
    else:
        pols = ", ".join(sorted({(re.search(r'\bp\s*=\s*(\w+)', d, re.I) or [None, '?'])[1] for d in dm}))
        checks.append(_check("dmarc", "DMARC record", "fail",
                             f"{len(dm)} DMARC records found (policies: {pols}). With more than one, receivers ignore DMARC",
                             f"Delete all but one TXT record at _dmarc.{domain}. Start with p=none, move to quarantine once reports look clean"))

    mx = safe(dns.mx, domain)
    checks.append(_check("mx", "Replies can arrive (MX)", "pass" if isinstance(mx, list) and mx else "fail",
                         ", ".join(mx[:3]) if isinstance(mx, list) and mx else f"No MX record on {domain}",
                         None if isinstance(mx, list) and mx else "Add the MX record your mail host gives you"))

    if hiox and isinstance(server_ips, list) and server_ips:
        ptr = safe(dns.ptr, server_ips[0])
        ok_ptr = isinstance(ptr, list) and bool(ptr)
        fwd = ok_ptr and server_ips[0] in (safe(dns.a, ptr[0]) or [])
        checks.append(_check("ptr", "Mail server reverse DNS", "pass" if fwd else ("warn" if ok_ptr else "fail"),
                             f"{server_ips[0]} -> {ptr[0]}" if ok_ptr else f"{server_ips[0]} has no reverse DNS",
                             None if fwd else "Ask the mail host (HIOX) to set reverse DNS for the server"))
        bl = dns.blocklist(f"{ipaddress.ip_address(server_ips[0]).reverse_pointer.replace('.in-addr.arpa', '')}.zen.spamhaus.org")
        checks.append(_check("ip_blocklist", "Mail server not on a spam blocklist (Spamhaus)",
                             {"clean": "pass", "listed": "fail"}.get(bl, "warn"),
                             {"clean": "Not listed", "listed": f"{server_ips[0]} is listed on Spamhaus ZEN"}.get(bl, "Couldn't check from here"),
                             "Ask the mail host to delist the server or move you to a clean one" if bl == "listed" else None))
    bl = dns.blocklist(f"{domain}.dbl.spamhaus.org")
    checks.append(_check("domain_blocklist", "Domain not on a spam blocklist (Spamhaus)",
                         {"clean": "pass", "listed": "fail"}.get(bl, "warn"),
                         {"clean": "Not listed", "listed": f"{domain} is listed on Spamhaus DBL"}.get(bl, "Couldn't check from here")))

    # wording ------------------------------------------------------------------------------------------------
    if "initial" not in tpls:
        checks.append(_check("template_initial", "First email approved", "fail", "No approved first-email template",
                             "Email templates: write or pick a format and approve it"))
    else:
        issues = template_issues(*tpls["initial"])
        checks.append(_check("template_initial", "First email approved", "warn" if issues else "pass",
                             "; ".join(issues) if issues else "Approved; no spam signals"))
    missing = [str(i) for i in range(1, len(fdays) + 1) if f"followup_{i}" not in tpls]
    flagged = [f"#{i}: {'; '.join(template_issues(*tpls[f'followup_{i}']))}" for i in range(1, len(fdays) + 1)
               if f"followup_{i}" in tpls and template_issues(*tpls[f"followup_{i}"])]
    checks.append(_check("template_followups", f"Follow-up emails approved ({len(fdays)})",
                         "warn" if missing or flagged else "pass",
                         (f"Not approved yet: follow-up {', '.join(missing)} (those follow-ups wait)" if missing else "All approved")
                         + (f"; {' / '.join(flagged)}" if flagged else ""),
                         "Email templates: approve a template for each follow-up" if missing else None))
    return _store(database_url, checks)


def _store(database_url: str, checks: list[dict]) -> dict:
    pre = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "checks": checks,
           "ok": not any(c["status"] == "fail" for c in checks)}
    with db.tx(database_url) as conn:
        conn.execute("""INSERT INTO flightbridge.system_setting (key, value) VALUES ('send_autopilot_state', %s::jsonb)
                        ON CONFLICT (key) DO UPDATE SET value = flightbridge.system_setting.value || EXCLUDED.value,
                        updated_at = now()""", (json.dumps({"preflight": pre}),))
    return pre
