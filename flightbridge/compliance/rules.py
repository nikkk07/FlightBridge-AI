"""Load and validate the jurisdiction rule file. A malformed file raises at load time."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "jurisdictions.json"
MODES = {"ALLOW_IF_CONDITIONS", "HUMAN_REVIEW", "BLOCK"}
METHODS = {"automated_extraction", "human_verified"}


class RulesError(ValueError):
    pass


def validate(rules: dict) -> dict:
    from .content import VALIDATORS  # late import: content depends on nothing here

    policies = rules.get("fact_policies", {})
    for fact, pol in policies.items():
        if not set(pol.get("allowed_methods", [])) <= METHODS or not pol.get("allowed_methods"):
            raise RulesError(f"fact_policies.{fact}: invalid allowed_methods")
        if int(pol.get("max_age_days", 0)) <= 0:
            raise RulesError(f"fact_policies.{fact}: max_age_days must be > 0")
    for req in rules.get("global_content_requirements", []):
        if req not in VALIDATORS:
            raise RulesError(f"unknown global content requirement {req}")
    for key, c in rules["countries"].items():
        for addr in ("role_address", "named_person_address"):
            spec = c.get(addr)
            if not spec or spec.get("mode") not in MODES:
                raise RulesError(f"{key}.{addr}: invalid mode")
            for f in spec.get("legal_conditions", []):
                if f not in policies:
                    raise RulesError(f"{key}.{addr}: condition {f} has no fact_policy")
            for r in spec.get("extra_content_requirements", []):
                if r not in VALIDATORS:
                    raise RulesError(f"{key}.{addr}: unknown content requirement {r}")
        for f in c.get("block_facts", []):
            if f not in policies:
                raise RulesError(f"{key}: block fact {f} has no fact_policy")
        for r in c.get("content_requirements", []):
            if r not in VALIDATORS:
                raise RulesError(f"{key}: unknown content requirement {r}")
    return rules


@lru_cache(maxsize=4)
def load_rules(path: str = str(RULES_PATH)) -> dict:
    with open(path, encoding="utf-8") as fh:
        return validate(json.load(fh))


def rule_for(country: str, rules: dict) -> tuple[str, dict | None]:
    c = (country or "").upper()
    if c in rules["countries"] and not c.startswith("EU_"):
        return c, rules["countries"][c]
    if c in rules.get("eu_member_states", []):
        return "EU_DEFAULT", rules["countries"]["EU_DEFAULT"]
    return c, None
