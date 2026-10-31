from datetime import timedelta

import pytest

from flightbridge.compliance.jurisdiction import Evidence, RecipientContext, evaluate
from flightbridge.compliance.rules import RulesError, validate
from flightbridge.compliance.suppression import SuppressionIndex

NONE = SuppressionIndex()
EMP = "00000000-0000-0000-0000-000000000001"


def ev(fact, now, method="human_verified", by=EMP, age_days=0, excerpt="evidence text", url="https://school.example/contact"):
    return Evidence(fact, url, excerpt, method, now - timedelta(days=age_days), by if method == "human_verified" else None)


def ctx(country, role=True, evidence=(), email="info@school.example", org="org-1"):
    return RecipientContext(email, country, role, org, tuple(evidence))


def all_condition_cases():
    from flightbridge.compliance.rules import load_rules
    r = load_rules()
    for key, c in r["countries"].items():
        if key.startswith("EU_"):
            continue
        for addr in ("role_address", "named_person_address"):
            spec = c[addr]
            if spec["mode"] == "ALLOW_IF_CONDITIONS" and spec.get("legal_conditions"):
                for cond in spec["legal_conditions"]:
                    yield key, addr == "role_address", tuple(spec["legal_conditions"]), cond


@pytest.mark.parametrize("country,role,conditions,dropped", list(all_condition_cases()))
def test_every_missing_legal_condition_prevents_allow(rules, now, country, role, conditions, dropped):
    full = [ev(c, now) for c in conditions]
    assert evaluate(ctx(country, role, full), rules, NONE, now).decision == "ALLOW"
    partial = [e for e in full if e.fact != dropped]
    r = evaluate(ctx(country, role, partial), rules, NONE, now)
    assert r.decision != "ALLOW"
    assert f"unverified_condition:{dropped}" in r.reasons


def test_unknown_country_blocks(rules, now):
    assert evaluate(ctx("ZZ"), rules, NONE, now).decision == "BLOCK"


def test_germany_blocks_even_with_evidence(rules, now):
    r = evaluate(ctx("DE", evidence=[ev("recipient_is_corporate_subscriber", now)]), rules, NONE, now)
    assert r.decision == "BLOCK"


def test_other_eu_and_au_in_review(rules, now):
    assert evaluate(ctx("FR"), rules, NONE, now).decision == "HUMAN_REVIEW"
    assert evaluate(ctx("AU"), rules, NONE, now).decision == "HUMAN_REVIEW"
    assert evaluate(ctx("IN"), rules, NONE, now).decision == "HUMAN_REVIEW"


def test_us_allows_and_returns_content_requirements(rules, now):
    r = evaluate(ctx("US"), rules, NONE, now)
    assert r.decision == "ALLOW"
    for req in ("physical_postal_address", "ad_disclosure", "opt_out_mechanism", "approved_claims_only", "ref_token"):
        assert req in r.content_requirements


def test_automated_evidence_rejected_for_human_only_fact(rules, now):
    conds = ["address_conspicuously_published", "no_refusal_statement_near_address", "message_relevant_to_business_role"]
    evs = [ev(c, now) for c in conds[:2]] + [ev(conds[2], now, method="automated_extraction")]
    r = evaluate(ctx("CA", evidence=evs), rules, NONE, now)
    assert r.decision == "HUMAN_REVIEW"
    assert any("method_not_allowed" in x for x in r.reasons)


@pytest.mark.parametrize("bad", [
    {"age_days": 400},                 # expired (365-day policy)
    {"excerpt": "   "},                # empty excerpt
    {"url": "ftp://x"},                # non-http source
    {"by": None},                      # human_verified without verifier
    {"age_days": -2},                  # verified in the future
])
def test_invalid_evidence_does_not_count(rules, now, bad):
    e = ev("recipient_is_corporate_subscriber", now, **bad)
    r = evaluate(ctx("GB", evidence=[e]), rules, NONE, now)
    assert r.decision == "HUMAN_REVIEW"


def test_block_fact_overrides_conditions(rules, now):
    conds = ["address_conspicuously_published", "no_refusal_statement_near_address", "message_relevant_to_business_role"]
    evs = [ev(c, now) for c in conds] + [ev("refusal_statement_present", now, method="automated_extraction")]
    assert evaluate(ctx("CA", evidence=evs), rules, NONE, now).decision == "BLOCK"


def test_uk_sole_trader_blocked(rules, now):
    evs = [ev("recipient_is_corporate_subscriber", now), ev("recipient_is_sole_trader_or_non_scottish_partnership", now)]
    assert evaluate(ctx("GB", evidence=evs), rules, NONE, now).decision == "BLOCK"


def test_uk_named_person_needs_lia_and_privacy_notice(rules, now):
    r = evaluate(ctx("GB", role=False, evidence=[ev("recipient_is_corporate_subscriber", now)]), rules, NONE, now)
    assert r.decision == "HUMAN_REVIEW"
    r2 = evaluate(ctx("GB", role=False, evidence=[ev("recipient_is_corporate_subscriber", now),
                                                  ev("legitimate_interests_assessment_done", now)]), rules, NONE, now)
    assert r2.decision == "ALLOW" and "privacy_notice" in r2.content_requirements


@pytest.mark.parametrize("email", ["no-at-sign", " info@x.example", "a@b@c.example"])
def test_invalid_email_blocks(rules, now, email):
    assert evaluate(ctx("US", email=email), rules, NONE, now).decision == "BLOCK"


def test_suppressed_contact_blocks_before_rules(rules, now):
    from flightbridge.compliance.suppression import email_hash
    s = SuppressionIndex(contact_hashes=[email_hash("info@school.example")])
    r = evaluate(ctx("US"), rules, s, now)
    assert r.decision == "BLOCK" and r.reasons == ["suppressed:contact"]


def test_rules_validation_rejects_unknown_requirement(rules):
    import copy
    bad = copy.deepcopy(rules)
    bad["countries"]["US"]["content_requirements"].append("made_up_requirement")
    with pytest.raises(RulesError):
        validate(bad)


def test_rules_validation_rejects_condition_without_policy(rules):
    import copy
    bad = copy.deepcopy(rules)
    bad["countries"]["GB"]["role_address"]["legal_conditions"].append("no_policy_fact")
    with pytest.raises(RulesError):
        validate(bad)
