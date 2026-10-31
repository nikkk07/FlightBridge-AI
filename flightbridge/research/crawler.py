"""Bounded, same-site, robots-aware crawl of one school website."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .extract import EmailFinding, parse, site_domain
from .fetch import FetchBlocked, SafeFetcher


@dataclass
class CrawlResult:
    start_url: str
    pages: list = field(default_factory=list)      # (url, status, robots_allowed, sha256)
    findings: list = field(default_factory=list)   # EmailFinding
    errors: list = field(default_factory=list)


def crawl(start_url: str, fetcher: SafeFetcher, max_pages: int = 12, max_depth: int = 2) -> CrawlResult:
    res = CrawlResult(start_url)
    site = site_domain(start_url)
    queue, seen, emails = [(start_url, 0)], set(), {}
    while queue and len(res.pages) < max_pages:
        url, depth = queue.pop(0)
        if url in seen or site_domain(url) != site:
            continue
        seen.add(url)
        try:
            page = fetcher.get(url)
        except FetchBlocked as exc:
            res.errors.append(f"{url}: blocked:{exc}")
            continue
        except Exception as exc:  # network errors are recorded, never fatal for the crawl
            res.errors.append(f"{url}: {type(exc).__name__}")
            continue
        sha = hashlib.sha256(page.html.encode()).hexdigest() if page.html else None
        res.pages.append((page.url, page.status, page.robots_allowed, sha))
        if page.status in (301, 302, 303, 307, 308) and page.url != url:
            queue.insert(0, (page.url, depth))
            continue
        if not page.html:
            continue
        found, links = parse(page.html, page.url)
        for f in found:
            emails.setdefault(f.email, f)
        if depth < max_depth:
            queue.extend((u, depth + 1) for u in links if u not in seen)
    res.findings = list(emails.values())
    return res
