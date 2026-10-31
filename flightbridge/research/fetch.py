"""Safe HTTP fetcher for school websites: SSRF guard, robots.txt, size/type limits, politeness delay."""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import requests

MAX_BYTES = 2_000_000
ALLOWED_TYPES = ("text/html", "application/xhtml+xml")


class FetchBlocked(Exception):
    pass


@dataclass
class Page:
    url: str
    status: int
    html: str
    robots_allowed: bool


def host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


class SafeFetcher:
    def __init__(self, user_agent: str, delay_seconds: float = 2.0, timeout: float = 15.0,
                 allow_private_hosts: bool = False):
        self.ua, self.delay, self.timeout = user_agent, delay_seconds, timeout
        self.allow_private = allow_private_hosts    # True only in tests
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._last: dict[str, float] = {}
        self._s = requests.Session()
        self._s.headers["User-Agent"] = user_agent

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchBlocked("scheme_or_host")
        if parts.username or parts.password:
            raise FetchBlocked("credentials_in_url")
        if not self.allow_private and not host_is_public(parts.hostname):
            raise FetchBlocked("non_public_host")

    def robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                r = self._s.get(urljoin(base, "/robots.txt"), timeout=self.timeout, allow_redirects=False)
                if r.status_code in (401, 403):
                    rp.disallow_all = True
                elif r.status_code >= 400:
                    rp.allow_all = True
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp.disallow_all = True       # cannot read robots => do not crawl
            self._robots[base] = rp
        return rp.can_fetch(self.ua, url)

    def _polite_wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str) -> Page:
        self._check_url(url)
        if not self.robots_allows(url):
            return Page(url, 0, "", False)
        self._polite_wait(urlsplit(url).netloc)
        r = self._s.get(url, timeout=self.timeout, stream=True, allow_redirects=False)
        try:
            if 300 <= r.status_code < 400 and r.headers.get("Location"):
                target = urljoin(url, r.headers["Location"])
                self._check_url(target)            # redirects re-pass the SSRF guard
                return Page(target, r.status_code, "", True)
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if r.status_code != 200 or ctype not in ALLOWED_TYPES:
                return Page(url, r.status_code, "", True)
            buf = b""
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) > MAX_BYTES:
                    break
            return Page(url, r.status_code, buf[:MAX_BYTES].decode(r.encoding or "utf-8", "replace"), True)
        finally:
            r.close()
