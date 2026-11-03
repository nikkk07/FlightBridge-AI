"""Bounded, same-site, robots-aware crawl of one school website.

Contact pages are read first: the usual paths (/contact, /about, /admissions, /privacy …) are tried up front, and links
that look like contact/about pages jump the queue. If the home page redirects to the school's new domain (a rebrand),
the crawl follows it and treats both domains as the school's own.
"""
from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass, field

from .extract import PRIORITY_PATH, EmailFinding, parse_page, site_domain
from .fetch import FetchBlocked, SafeFetcher
from .signals import extract_phones, visible_text

CONTACT_PATHS = ("contact", "contact-us", "contactus", "contact.html", "about", "about-us", "admissions",
                 "international-students", "enquire", "get-in-touch", "privacy-policy", "privacy")


@dataclass
class CrawlResult:
    start_url: str
    pages: list = field(default_factory=list)      # (url, status, robots_allowed, sha256)
    findings: list = field(default_factory=list)   # EmailFinding
    errors: list = field(default_factory=list)
    texts: list = field(default_factory=list)      # (url, visible text) for signal extraction
    phones: list = field(default_factory=list)     # [{phone, display, source_url, how}] published by the site
    socials: list = field(default_factory=list)    # [(kind, url, source_url)] LinkedIn/Facebook/… linked from the site
    contact_forms: list = field(default_factory=list)  # pages with an enquiry form
    sites: tuple = ()                              # the school's own domain(s), incl. a redirect target


def crawl(start_url: str, fetcher: SafeFetcher, max_pages: int = 12, max_depth: int = 2,
          seed_contact_paths: bool = False) -> CrawlResult:
    res = CrawlResult(start_url)
    sites = [site_domain(start_url)]
    base = start_url.rstrip("/")
    order = 0
    heap: list = []

    def push(url, depth, prio):
        nonlocal order
        order += 1
        heapq.heappush(heap, (prio, order, url, depth))

    push(start_url, 0, 0)
    if seed_contact_paths:
        for p in CONTACT_PATHS:
            push(f"{base}/{p}", 1, 1)
    seen, emails, socials = set(), {}, {}
    misses = 0
    while heap and len(res.pages) < max_pages:
        _, _, url, depth = heapq.heappop(heap)
        if url in seen or site_domain(url) not in sites:
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
        if page.status == 404 and depth == 1 and seed_contact_paths:
            misses += 1                      # guessed path that doesn't exist: not worth recording
            continue
        sha = hashlib.sha256(page.html.encode()).hexdigest() if page.html else None
        res.pages.append((page.url, page.status, page.robots_allowed, sha))
        if page.status in (301, 302, 303, 307, 308) and page.url != url:
            target = site_domain(page.url)
            if depth == 0 and target not in sites:
                sites.append(target)         # home page moved to the school's new domain: follow it
            push(page.url, depth, 0)
            continue
        if not page.html:
            continue
        found, links, extras = parse_page(page.html, page.url, tuple(s for s in sites if s != site_domain(page.url)))
        res.texts.append((page.url, visible_text(page.html)))
        for ph in extract_phones(page.html, page.url):
            if ph["phone"] not in {p["phone"] for p in res.phones}:
                res.phones.append(ph)
        for f in found:
            emails.setdefault(f.email, f)
        for kind, u in extras.socials:
            socials.setdefault(u, (kind, u, page.url))
        if extras.contact_form and page.url not in res.contact_forms and len(res.contact_forms) < 3:
            res.contact_forms.append(page.url)
        if depth < max_depth:
            for u in links:
                if u not in seen:
                    push(u, depth + 1, 1 if PRIORITY_PATH.search(u) else 2 + depth)
    res.findings = list(emails.values())
    res.socials = list(socials.values())
    res.sites = tuple(sites)
    return res
