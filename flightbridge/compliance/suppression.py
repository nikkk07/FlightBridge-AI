"""Three-scope suppression: contact (address hash), organisation, domain.

Domain scope never applies to shared mailbox providers (gmail.com, outlook.com ...),
so one opt-out from a Gmail user cannot silence unrelated organisations.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

DEFAULT_PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "yahoo.co.in", "yahoo.co.uk", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "web.de", "zoho.com", "yandex.com", "mail.com", "rediffmail.com", "bigpond.com",
})


def normalise_email(email: str) -> str:
    return email.strip().lower()


def email_hash(email: str) -> str:
    return hashlib.sha256(normalise_email(email).encode()).hexdigest()


def email_domain(email: str) -> str:
    return normalise_email(email).rsplit("@", 1)[-1]


class SharedProviderDomainError(ValueError):
    pass


class SuppressionIndex:
    def __init__(self, contact_hashes: Iterable[str] = (), org_ids: Iterable[str] = (),
                 domains: Iterable[str] = (), public_domains: Iterable[str] = DEFAULT_PUBLIC_DOMAINS):
        self.public = frozenset(d.lower() for d in public_domains)
        self.contacts = set(contact_hashes)
        self.orgs = {str(o) for o in org_ids}
        self.domains = set()
        for d in domains:
            self.add_domain(d)

    def add_domain(self, domain: str) -> None:
        d = domain.strip().lower()
        if d in self.public:
            raise SharedProviderDomainError(d)
        self.domains.add(d)

    def match(self, email: str, org_id: str | None) -> str | None:
        if email_hash(email) in self.contacts:
            return "contact"
        if org_id is not None and str(org_id) in self.orgs:
            return "organisation"
        dom = email_domain(email)
        if dom not in self.public and dom in self.domains:
            return "domain"
        return None
