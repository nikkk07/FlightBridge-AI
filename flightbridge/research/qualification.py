"""Explainable, configurable qualification score (0-100). Not a regulatory certification.

Initial weights (rationale; tune from pilot outcomes, version bumps on any change):
  regulatory 30  - unverified institutions are excluded from outreach regardless of score
  cpl_ppl    20  - We One's students need CPL/PPL flying after DGCA theory
  contact    15  - no usable public business address => no outreach
  intl       15  - published international-student intake indicates referral fit
  operating  10  - active website / recent content
  fleet      10  - published fleet signals training capacity
"""
from __future__ import annotations

WEIGHTS_VERSION = "2026-10-30.1"
WEIGHTS = {"regulatory": 30, "cpl_ppl": 20, "contact": 15, "intl": 15, "operating": 10, "fleet": 10}
assert sum(WEIGHTS.values()) == 100

CPL_RE = r"\b(CPL|commercial pilot)"
PPL_RE = r"\b(PPL|private pilot)"


def score(signals: dict) -> dict:
    """signals: regulatory_status, classification, has_cpl, has_ppl, role_contact, any_contact,
    intl_students, site_active, fleet_published (bools/strings)."""
    b, missing, risks = {}, [], []
    reg = signals.get("regulatory_status")
    cls = signals.get("classification") or ""
    if reg in ("listed_in_register", "verified_by_human") and cls not in ("PART61_OPERATOR_UNVERIFIED", "UNCLASSIFIED"):
        b["regulatory"] = WEIGHTS["regulatory"]
    else:
        b["regulatory"] = 0
        missing.append("regulatory_verification")
    if cls in ("DTO_UK", "DTO_EU_NCA"):
        risks.append("dto_ppl_level_only")
    b["cpl_ppl"] = WEIGHTS["cpl_ppl"] if signals.get("has_cpl") else (WEIGHTS["cpl_ppl"] // 2 if signals.get("has_ppl") else 0)
    if not signals.get("has_cpl"):
        missing.append("cpl_programme_evidence")
    b["contact"] = WEIGHTS["contact"] if signals.get("role_contact") else (8 if signals.get("any_contact") else 0)
    if not signals.get("any_contact"):
        missing.append("public_business_email")
    b["intl"] = WEIGHTS["intl"] if signals.get("intl_students") else 0
    b["operating"] = WEIGHTS["operating"] if signals.get("site_active") else 0
    b["fleet"] = WEIGHTS["fleet"] if signals.get("fleet_published") else 0
    total = sum(b.values())
    if b["regulatory"] == 0 or not signals.get("any_contact"):
        action = "needs_human_review" if signals.get("any_contact") else "reject:no_public_contact"
    elif total >= 60:
        action = "draft_outreach_for_approval"
    else:
        action = "needs_human_review"
    known = sum(1 for k in ("has_cpl", "intl_students", "fleet_published", "site_active") if k in signals)
    confidence = "high" if known == 4 and b["regulatory"] else "medium" if known >= 2 else "low"
    return {"score": total, "weights_version": WEIGHTS_VERSION, "breakdown": b, "missing": missing,
            "risks": risks, "recommended_action": action, "confidence": confidence}
