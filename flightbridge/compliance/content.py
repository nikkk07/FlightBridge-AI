"""Gate 2 of 2: email-content readiness.

Every requirement named by the jurisdiction result must pass a deterministic
validator against the exact subject/body that will be sent. Unknown requirement
=> FAIL. Missing sender-profile value (e.g. postal address not yet signed off)
=> FAIL. Nothing here is advisory metadata.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class SenderProfile:
    legal_name: str | None
    postal_address: str | None
    contact_email: str | None
    phone: str | None
    opt_out_text: str | None
    ad_disclosure_text: str | None
    privacy_notice_text: str | None


@dataclass(frozen=True)
class OutboundContent:
    subject: str
    body_text: str
    kind: str          # initial | followup | reply
    ref_token: str


@dataclass
class ContentResult:
    decision: str                      # PASS | FAIL
    content_sha256: str
    failures: list = field(default_factory=list)
    passed: list = field(default_factory=list)


def content_sha256(subject: str, body_text: str) -> str:
    return hashlib.sha256(f"{subject}\n\x00\n{body_text}".encode()).hexdigest()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _contains(body: str, needle: str | None) -> bool:
    return bool(needle) and _norm(needle) in _norm(body)


def _require_profile(value: str | None, name: str) -> str | None:
    return None if value and value.strip() else f"profile_value_missing:{name}"


# Claims banned by docs/COMPANY_FACTS_RECONCILIATION.md (unverified or contradicted).
BANNED_CLAIMS = [
    (r"\b\d[\d,]*\s*\+?\s*(pilots|students|candidates|graduates)\b", "unverified_volume_claim"),
    (r"100\s*%\s*placement", "unverified_placement_claim"),
    (r"\bplacement (guarantee|assured|assistance)\b", "unverified_placement_claim"),
    (r"\bguarantee(d)?\b.*\b(job|placement|licen[cs]e|pass)\b", "guarantee_claim"),
    (r"\bour (flying school|aircraft|fleet)\b", "operates_aircraft_claim"),
    (r"\bDGCA[- ]approved\b", "unverified_regulatory_claim"),
    (r"\b(approved|certified|registered) (FTO|flying training organi[sz]ation|ATO)\b", "unverified_regulatory_claim"),
    (r"\bsince 2011\b", "contradicted_founding_year"),
    (r"\b(airline partners?|partner airlines?)\b", "unverified_partner_claim"),
    (r"\b(india'?s )?(most trusted|best|no\.? ?1|#1|premier|leading)\b", "superlative_claim"),
    (r"flystar|flying star", "other_brand"),
]
PLACEHOLDER = re.compile(r"\{\{|\}\}|\[\[|\]\]|PLACEHOLDER|\bTODO\b|lorem ipsum|<insert|\bXXX\b", re.I)
REPLY_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:", re.I)


def v_sender_identification(c, p):
    return _require_profile(p.legal_name, "legal_name") or (
        None if _contains(c.body_text, p.legal_name) else "sender_name_absent")


def v_physical_postal_address(c, p):
    return _require_profile(p.postal_address, "postal_address") or (
        None if _contains(c.body_text, p.postal_address) else "postal_address_absent")


def v_contact_details(c, p):
    if not (p.contact_email or p.phone):
        return "profile_value_missing:contact_email_or_phone"
    return None if (_contains(c.body_text, p.contact_email) or _contains(c.body_text, p.phone)) else "contact_details_absent"


def v_opt_out_mechanism(c, p):
    return _require_profile(p.opt_out_text, "opt_out_text") or (
        None if _contains(c.body_text, p.opt_out_text) else "opt_out_absent")


def v_ad_disclosure(c, p):
    return _require_profile(p.ad_disclosure_text, "ad_disclosure_text") or (
        None if _contains(c.body_text, p.ad_disclosure_text) else "ad_disclosure_absent")


def v_privacy_notice(c, p):
    return _require_profile(p.privacy_notice_text, "privacy_notice_text") or (
        None if _contains(c.body_text, p.privacy_notice_text) else "privacy_notice_absent")


def v_non_deceptive_subject(c, p):
    s = c.subject or ""
    if not s.strip():
        return "subject_empty"
    if len(s) > 150:
        return "subject_too_long"
    if c.kind != "reply" and REPLY_PREFIX.match(s):
        return "fake_reply_prefix"
    if re.search(r"\b(urgent|act now|final notice|winner|free money)\b", s, re.I):
        return "pressure_or_misleading_subject"
    return None


def v_no_placeholders(c, p):
    return "unresolved_placeholder" if PLACEHOLDER.search(c.subject + "\n" + c.body_text) else None


def v_approved_claims_only(c, p):
    text = c.subject + "\n" + c.body_text
    hits = sorted({tag for pat, tag in BANNED_CLAIMS if re.search(pat, text, re.I)})
    return ("banned_claims:" + ",".join(hits)) if hits else None


def v_ref_token(c, p):
    if not re.fullmatch(r"FB-[0-9A-F]{12}", c.ref_token or ""):
        return "ref_token_malformed"
    return None if c.ref_token in c.body_text else "ref_token_absent"


VALIDATORS: dict[str, Callable[[OutboundContent, SenderProfile], str | None]] = {
    "sender_identification": v_sender_identification,
    "physical_postal_address": v_physical_postal_address,
    "contact_details": v_contact_details,
    "opt_out_mechanism": v_opt_out_mechanism,
    "ad_disclosure": v_ad_disclosure,
    "privacy_notice": v_privacy_notice,
    "non_deceptive_subject": v_non_deceptive_subject,
    "no_placeholders": v_no_placeholders,
    "approved_claims_only": v_approved_claims_only,
    "ref_token": v_ref_token,
}


def evaluate(content: OutboundContent, requirements: list[str], profile: SenderProfile) -> ContentResult:
    res = ContentResult("FAIL", content_sha256(content.subject, content.body_text))
    if not requirements:
        res.failures.append("no_requirements_supplied")   # a caller that skips gate 1 cannot pass gate 2
        return res
    for req in requirements:
        fn = VALIDATORS.get(req)
        if fn is None:
            res.failures.append(f"unknown_requirement:{req}")
            continue
        err = fn(content, profile)
        (res.failures.append(f"{req}:{err}") if err else res.passed.append(req))
    res.decision = "PASS" if not res.failures else "FAIL"
    return res
