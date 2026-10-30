"""Jurisdiction-aware outreach eligibility gate.

Pure, deterministic, dependency-free. Every send path must call `evaluate()`
and must refuse to send unless the decision is ALLOW. The gate can only make
outreach *stricter*: unknown country, missing facts or suppression => no send.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "jurisdictions.json"


class Decision(str, Enum):
    ALLOW = "ALLOW"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class Recipient:
    email: str
    country: str                      # ISO 3166-1 alpha-2 of the recipient organisation
    is_role_address: bool             # info@, admissions@ ... vs a named person
    facts: frozenset = frozenset()    # verified condition flags, e.g. "address_conspicuously_published"


@dataclass
class Result:
    decision: Decision
    rule: str
    reasons: list = field(default_factory=list)
    requires: list = field(default_factory=list)
    opt_out_max_business_days: int | None = None


def normalise_email(email: str) -> str:
    return email.strip().lower()


def email_hash(email: str) -> str:
    return hashlib.sha256(normalise_email(email).encode()).hexdigest()


def email_domain(email: str) -> str:
    return normalise_email(email).rsplit("@", 1)[-1]


class Suppression:
    """Address- and domain-level suppression. Membership is checked by hash."""

    def __init__(self, addresses: Iterable[str] = (), domains: Iterable[str] = ()):
        self._addr = {email_hash(a) for a in addresses}
        self._dom = {d.strip().lower() for d in domains}

    def is_suppressed(self, email: str) -> bool:
        return email_hash(email) in self._addr or email_domain(email) in self._dom


def load_rules(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _rule_for(country: str, rules: dict) -> tuple[str, dict | None]:
    c = country.upper()
    if c in rules["countries"]:
        return c, rules["countries"][c]
    if c in rules.get("eu_member_states", []):
        return "EU_DEFAULT", rules["countries"]["EU_DEFAULT"]
    return c, None


def evaluate(r: Recipient, rules: dict, suppression: Suppression) -> Result:
    if "@" not in r.email:
        return Result(Decision.BLOCK, "-", ["invalid_email"])
    if suppression.is_suppressed(r.email):
        return Result(Decision.BLOCK, "-", ["suppressed"])

    key, rule = _rule_for(r.country, rules)
    if rule is None:
        return Result(Decision.BLOCK, key, ["unconfigured_country"])

    res = Result(Decision.BLOCK, key, requires=list(rule.get("requires", [])),
                 opt_out_max_business_days=rule.get("opt_out_max_business_days"))

    block_if = rule.get("block_if", [])
    if "always" in block_if:
        res.reasons.append("jurisdiction_blocks_unsolicited_email")
        return res
    hits = [b for b in block_if if b in r.facts]
    if hits:
        res.reasons.extend(hits)
        return res

    mode = rule["role_address"] if r.is_role_address else rule["named_person_address"]
    if mode == "BLOCK":
        res.reasons.append("address_type_blocked")
        return res
    if mode == "HUMAN_REVIEW":
        res.decision = Decision.HUMAN_REVIEW
        res.reasons.append("rule_requires_human_review")
        return res
    if mode == "ALLOW_IF_CONDITIONS":
        missing = [c for c in rule.get("conditions", []) if c not in r.facts]
        if missing:
            res.decision = Decision.HUMAN_REVIEW
            res.reasons.extend(f"unverified:{m}" for m in missing)
            return res
    res.decision = Decision.ALLOW
    return res
