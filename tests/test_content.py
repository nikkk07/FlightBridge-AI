import dataclasses

import pytest

from flightbridge.compliance import content as cg
from flightbridge.compliance.content import OutboundContent
from flightbridge.compliance.jurisdiction import RecipientContext, evaluate as jeval
from flightbridge.compliance.suppression import SuppressionIndex
from flightbridge.sending.service import compose_body

from .conftest import FULL_PROFILE

REF = "FB-0123456789AB"
MAIN = "Dear team,\n\nWe One Aviation Academy teaches DGCA ground subjects in Dwarka, New Delhi."


def good(profile=FULL_PROFILE, subject="Student referral partnership enquiry", kind="initial"):
    return OutboundContent(subject, compose_body(MAIN, profile, REF), kind, REF)


def all_requirement_sets(rules):
    for key, c in rules["countries"].items():
        for addr in ("role_address", "named_person_address"):
            reqs = list(dict.fromkeys(rules["global_content_requirements"] + c["content_requirements"]
                                      + c[addr].get("extra_content_requirements", [])))
            for r in reqs:
                yield key, addr, reqs, r


# How to break each requirement: (profile mutation | content mutation)
BREAKERS = {
    "sender_identification":   lambda: (dataclasses.replace(FULL_PROFILE, legal_name=None), None),
    "physical_postal_address": lambda: (dataclasses.replace(FULL_PROFILE, postal_address=None), None),
    "contact_details":         lambda: (dataclasses.replace(FULL_PROFILE, contact_email=None, phone=None), None),
    "opt_out_mechanism":       lambda: (dataclasses.replace(FULL_PROFILE, opt_out_text=None), None),
    "ad_disclosure":           lambda: (dataclasses.replace(FULL_PROFILE, ad_disclosure_text=None), None),
    "privacy_notice":          lambda: (dataclasses.replace(FULL_PROFILE, privacy_notice_text=None), None),
    "non_deceptive_subject":   lambda: (FULL_PROFILE, {"subject": "RE: your enquiry"}),
    "no_placeholders":         lambda: (FULL_PROFILE, {"body_suffix": "\n{{school_name}}"}),
    "approved_claims_only":    lambda: (FULL_PROFILE, {"body_suffix": "\nWe have trained 3000+ pilots."}),
    "ref_token":               lambda: (FULL_PROFILE, {"ref": "FB-FFFFFFFFFFFF"}),
}


def test_every_requirement_has_a_breaker():
    assert set(BREAKERS) == set(cg.VALIDATORS)


def test_full_message_passes_every_jurisdiction(rules):
    for key, addr, reqs, _ in all_requirement_sets(rules):
        r = cg.evaluate(good(), reqs, FULL_PROFILE)
        assert r.decision == "PASS", (key, addr, r.failures)


@pytest.mark.parametrize("country,addr,reqs,req", [pytest.param(*x, id=f"{x[0]}-{x[1]}-{x[3]}")
                                                    for x in all_requirement_sets(__import__(
                                                        "flightbridge.compliance.rules", fromlist=["x"]).load_rules())])
def test_every_missing_mandatory_requirement_blocks(country, addr, reqs, req):
    profile, mut = BREAKERS[req]()
    mut = mut or {}
    ref = mut.get("ref", REF)
    body = compose_body(MAIN, profile, REF) + mut.get("body_suffix", "")
    c = OutboundContent(mut.get("subject", "Student referral partnership enquiry"), body, "initial", ref)
    r = cg.evaluate(c, reqs, profile)
    assert r.decision == "FAIL"
    assert any(f.startswith(req + ":") for f in r.failures), r.failures


def test_present_in_profile_but_absent_from_body_fails():
    c = OutboundContent("Partnership enquiry", f"{MAIN}\nRef: {REF}", "initial", REF)  # no footer at all
    r = cg.evaluate(c, ["physical_postal_address", "opt_out_mechanism", "ad_disclosure"], FULL_PROFILE)
    assert r.decision == "FAIL" and len(r.failures) == 3


def test_unknown_requirement_fails_closed():
    assert cg.evaluate(good(), ["sender_identification", "mystery"], FULL_PROFILE).decision == "FAIL"


def test_empty_requirement_list_fails_so_gate2_cannot_run_without_gate1():
    assert cg.evaluate(good(), [], FULL_PROFILE).failures == ["no_requirements_supplied"]


def test_real_sender_profile_blocks_us_until_address_signed_off(rules):
    from flightbridge.settings import load_sender_profile
    p = load_sender_profile()
    j = jeval(RecipientContext("info@x.example", "US", True, "o"), rules, SuppressionIndex())
    r = cg.evaluate(OutboundContent("Partnership enquiry", compose_body(MAIN, p, REF), "initial", REF),
                    j.content_requirements, p)
    assert r.decision == "FAIL"
    assert "physical_postal_address:profile_value_missing:postal_address" in r.failures


@pytest.mark.parametrize("claim", ["our flying school", "DGCA-approved", "100% placement", "since 2011",
                                   "India's most trusted", "partner airlines", "Flying Star", "6,000 students"])
def test_banned_claims(claim):
    c = OutboundContent("Partnership", compose_body(MAIN + " " + claim, FULL_PROFILE, REF), "initial", REF)
    assert cg.evaluate(c, ["approved_claims_only"], FULL_PROFILE).decision == "FAIL"


def test_reply_may_use_re_prefix():
    c = good(subject="Re: partnership", kind="reply")
    assert cg.evaluate(c, ["non_deceptive_subject"], FULL_PROFILE).decision == "PASS"
