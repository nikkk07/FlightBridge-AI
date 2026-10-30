from flightbridge.compliance.eligibility import (
    Decision, Recipient, Suppression, evaluate, load_rules,
)

RULES = load_rules()
NONE = Suppression()


def ev(email, country, role=True, facts=(), sup=NONE):
    return evaluate(Recipient(email, country, role, frozenset(facts)), RULES, sup)


def test_unknown_country_blocks():
    assert ev("info@school.example", "ZZ").decision is Decision.BLOCK


def test_us_role_address_allowed_with_requirements():
    r = ev("info@school.example", "US")
    assert r.decision is Decision.ALLOW
    assert "physical_postal_address" in r.requires and r.opt_out_max_business_days == 10


def test_suppressed_address_blocks_everywhere():
    sup = Suppression(addresses=["Info@School.example "])
    assert ev("info@school.example", "US", sup=sup).decision is Decision.BLOCK


def test_domain_suppression_blocks_other_addresses():
    sup = Suppression(domains=["school.example"])
    assert ev("ops@school.example", "US", sup=sup).reasons == ["suppressed"]


def test_germany_always_blocked():
    assert ev("info@schule.example", "DE").decision is Decision.BLOCK


def test_other_eu_state_needs_review():
    r = ev("info@ecole.example", "FR")
    assert r.decision is Decision.HUMAN_REVIEW and r.rule == "EU_DEFAULT"


def test_canada_requires_all_casl_conditions():
    partial = ev("info@x.example", "CA", facts={"address_conspicuously_published"})
    assert partial.decision is Decision.HUMAN_REVIEW
    full = ev("info@x.example", "CA", facts={
        "address_conspicuously_published", "no_refusal_statement_near_address",
        "message_relevant_to_business_role"})
    assert full.decision is Decision.ALLOW


def test_canada_refusal_statement_blocks():
    r = ev("info@x.example", "CA", facts={"refusal_statement_present",
                                          "address_conspicuously_published"})
    assert r.decision is Decision.BLOCK


def test_uk_corporate_role_allowed_named_person_reviewed():
    assert ev("info@x.example", "GB", facts={"recipient_is_corporate_subscriber"}).decision is Decision.ALLOW
    assert ev("jane@x.example", "GB", role=False,
              facts={"recipient_is_corporate_subscriber"}).decision is Decision.HUMAN_REVIEW


def test_uk_sole_trader_blocked():
    r = ev("info@x.example", "GB", facts={"recipient_is_sole_trader_or_non_scottish_partnership",
                                          "recipient_is_corporate_subscriber"})
    assert r.decision is Decision.BLOCK


def test_uk_unknown_entity_type_reviewed():
    assert ev("info@x.example", "GB").decision is Decision.HUMAN_REVIEW


def test_australia_and_india_review():
    assert ev("info@x.example", "AU").decision is Decision.HUMAN_REVIEW
    assert ev("info@x.example", "IN").decision is Decision.HUMAN_REVIEW


def test_invalid_email_blocks():
    assert ev("not-an-email", "US").decision is Decision.BLOCK
