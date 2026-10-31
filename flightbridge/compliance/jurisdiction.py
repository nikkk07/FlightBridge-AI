"""Gate 1 of 2: jurisdiction eligibility.

ALLOW only when every legal condition for the recipient's country and address
type is backed by valid evidence and no blocking fact is evidenced. Suppression
is checked here too, so no eligible decision can exist for a suppressed contact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .rules import rule_for
from .suppression import SuppressionIndex


@dataclass(frozen=True)
class Evidence:
    fact: str
    source_url: str
    excerpt: str
    method: str                     # automated_extraction | human_verified
    verified_at: datetime
    verified_by: str | None = None  # employee id for human_verified


@dataclass(frozen=True)
class RecipientContext:
    email: str
    country: str
    is_role_address: bool
    org_id: str
    evidence: Sequence[Evidence] = ()


@dataclass
class JurisdictionResult:
    decision: str                           # ALLOW | HUMAN_REVIEW | BLOCK
    rule_key: str
    reasons: list = field(default_factory=list)
    content_requirements: list = field(default_factory=list)
    opt_out_max_business_days: int | None = None


def evidence_is_valid(ev: Evidence, rules: dict, now: datetime) -> tuple[bool, str]:
    pol = rules["fact_policies"].get(ev.fact)
    if pol is None:
        return False, "no_policy"
    if ev.method not in pol["allowed_methods"]:
        return False, f"method_not_allowed:{ev.method}"
    if not ev.source_url.lower().startswith(("http://", "https://")):
        return False, "bad_source_url"
    if not ev.excerpt or not ev.excerpt.strip():
        return False, "empty_excerpt"
    if ev.method == "human_verified" and not ev.verified_by:
        return False, "human_verifier_missing"
    if ev.verified_at > now + timedelta(minutes=5):
        return False, "verified_in_future"
    if now - ev.verified_at > timedelta(days=int(pol["max_age_days"])):
        return False, "expired"
    return True, "ok"


def _verified_facts(evidence: Sequence[Evidence], rules: dict, now: datetime) -> tuple[set, list]:
    ok, rejected = set(), []
    for ev in evidence:
        valid, why = evidence_is_valid(ev, rules, now)
        (ok.add(ev.fact) if valid else rejected.append(f"evidence_rejected:{ev.fact}:{why}"))
    return ok, rejected


def evaluate(ctx: RecipientContext, rules: dict, suppression: SuppressionIndex,
             now: datetime | None = None) -> JurisdictionResult:
    now = now or datetime.now(timezone.utc)
    if "@" not in ctx.email or ctx.email.strip() != ctx.email or ctx.email.count("@") != 1:
        return JurisdictionResult("BLOCK", "-", ["invalid_email"])
    hit = suppression.match(ctx.email, ctx.org_id)
    if hit:
        return JurisdictionResult("BLOCK", "-", [f"suppressed:{hit}"])

    key, rule = rule_for(ctx.country, rules)
    if rule is None:
        return JurisdictionResult("BLOCK", key or "-", ["unconfigured_country"])

    spec = rule["role_address"] if ctx.is_role_address else rule["named_person_address"]
    content_reqs = list(dict.fromkeys(
        rules.get("global_content_requirements", []) + rule.get("content_requirements", [])
        + spec.get("extra_content_requirements", [])))
    res = JurisdictionResult("BLOCK", key, content_requirements=content_reqs,
                             opt_out_max_business_days=rule.get("opt_out_max_business_days"))

    facts, rejected = _verified_facts(ctx.evidence, rules, now)
    res.reasons.extend(rejected)

    blocking = [f for f in rule.get("block_facts", []) if f in facts]
    if blocking:
        res.reasons.extend(f"blocking_fact:{f}" for f in blocking)
        return res
    if spec["mode"] == "BLOCK":
        res.reasons.append("jurisdiction_blocks_this_address_type")
        return res
    if spec["mode"] == "HUMAN_REVIEW":
        res.decision = "HUMAN_REVIEW"
        res.reasons.append("jurisdiction_requires_case_by_case_review")
        return res
    missing = [c for c in spec.get("legal_conditions", []) if c not in facts]
    if missing:
        res.decision = "HUMAN_REVIEW"
        res.reasons.extend(f"unverified_condition:{m}" for m in missing)
        return res
    res.decision = "ALLOW"
    return res
