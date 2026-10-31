"""Extract public contact emails from a page. Never guesses or constructs addresses."""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24})(?![\w-])")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]{1,64})\s*[\[\(\{]\s*at\s*[\]\)\}]\s*([A-Za-z0-9-]+(?:\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*[A-Za-z0-9-]+)+)", re.I)
ASSET_SUFFIX = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)
ROLE_LOCALPARTS = {
    "info", "contact", "admissions", "admission", "enquiries", "enquiry", "inquiries", "office", "admin",
    "partnerships", "partners", "partner", "bd", "business", "sales", "international", "training",
    "ops", "operations", "hello", "reception", "school", "academy", "flighttraining", "students",
}
REFUSAL_RE = re.compile(
    r"(no unsolicited|not (wish|want) to receive unsolicited|do not send (us )?(any )?(unsolicited|marketing|commercial|promotional)"
    r"|unsolicited (commercial )?(emails?|messages?) (are )?not (accepted|welcome)|no (marketing|sales) (emails?|enquiries))", re.I)
PRIORITY_PATH = re.compile(r"contact|about|admission|international|partner|enquir|team|staff|management", re.I)


@dataclass(frozen=True)
class EmailFinding:
    email: str
    source_url: str
    via: str                 # mailto | text | cfemail | obfuscated
    context: str             # surrounding visible text (evidence excerpt)
    is_role_address: bool
    on_site_domain: bool
    refusal_nearby: bool


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.mailtos: list[str] = []
        self.cf: list[str] = []
        self.text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "a" and a.get("href"):
            href = a["href"].strip()
            (self.mailtos if href.lower().startswith("mailto:") else self.links).append(href)
        if a.get("data-cfemail"):
            self.cf.append(a["data-cfemail"])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def decode_cfemail(hexstr: str) -> str | None:
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return None


def site_domain(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _context(text: str, email: str, width: int = 160) -> str:
    i = text.lower().find(email.split("@")[0].lower())
    if i < 0:
        return text[:width].strip()
    return " ".join(text[max(0, i - width): i + len(email) + width].split())


def parse(html: str, page_url: str) -> tuple[list[EmailFinding], list[str]]:
    c = _Collector()
    c.feed(html)
    text = htmllib.unescape(" ".join(c.text))
    site = site_domain(page_url)
    candidates: list[tuple[str, str]] = []
    for m in c.mailtos:
        addr = unquote(m[7:].split("?", 1)[0]).strip()
        for a in addr.split(","):
            candidates.append((a.strip(), "mailto"))
    for h in c.cf:
        d = decode_cfemail(h)
        if d:
            candidates.append((d, "cfemail"))
    for m in EMAIL_RE.finditer(text):
        candidates.append((m.group(1), "text"))
    for m in OBFUSCATED_RE.finditer(text):
        dom = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", m.group(2), flags=re.I)
        candidates.append((f"{m.group(1)}@{dom}", "obfuscated"))

    findings, seen = [], set()
    for raw, via in candidates:
        e = raw.strip().strip(".").lower()
        if not EMAIL_RE.fullmatch(e) or ASSET_SUFFIX.search(e) or e in seen:
            continue
        seen.add(e)
        local, dom = e.split("@", 1)
        ctx = _context(text, e)
        findings.append(EmailFinding(
            email=e, source_url=page_url, via=via, context=ctx or e,
            is_role_address=local.split("+")[0] in ROLE_LOCALPARTS,
            on_site_domain=bool(site) and (dom == site or dom.endswith("." + site)),
            refusal_nearby=bool(REFUSAL_RE.search(ctx)) or bool(REFUSAL_RE.search(text))))
    links = []
    for href in c.links:
        u = urljoin(page_url, href.split("#", 1)[0])
        if site_domain(u) == site and urlsplit(u).scheme in ("http", "https"):
            links.append(u)
    links.sort(key=lambda u: 0 if PRIORITY_PATH.search(u) else 1)
    return findings, list(dict.fromkeys(links))
