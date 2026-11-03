"""Import regulator-register rows (parsed PDF output or human lookups) with strict classification rules."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

AUTHORITY_CLASSES = {
    "DGCA":   {"FTO_DGCA"},
    "UK_CAA": {"ATO_UK", "DTO_UK"},
    "EU_NCA": {"ATO_EU_NCA", "DTO_EU_NCA"},
    "FAA":    {"PART141_FAA", "PART61_OPERATOR_UNVERIFIED"},
    "TC":     {"FTU_TC"},
    "CASA":   {"PART141_CASA", "PART142_CASA"},
    "CRICOS": {"CRICOS_PROVIDER"},
    "SEVP":   {"SEVP_M1"},
    "IRCC_DLI": {"DLI_CA"},
    "CAA_NZ": {"PART141_NZ"},
    "NZQA":   {"NZQA_PROVIDER"},
}
ID_SCHEME = {"DGCA": "dgca_fto", "UK_CAA": "uk_caa_ref", "FAA": "faa_cert", "TC": "tc_ftu",
             "CASA": "casa_arn", "EU_NCA": "eu_nca_ref", "CRICOS": "cricos_code", "SEVP": "sevp_id",
             "IRCC_DLI": "dli_number", "CAA_NZ": "caa_nz_ref", "NZQA": "nzqa_id"}
HUMAN_LOOKUP = {"FAA", "TC", "CASA", "SEVP", "IRCC_DLI", "CAA_NZ", "NZQA"}
REQUIRED = ("name", "country", "authority", "classification", "source_url")


class RegisterRowError(ValueError):
    pass


@dataclass(frozen=True)
class RegisterRow:
    name: str
    country: str
    authority: str
    classification: str
    approval_ref: str | None
    source_url: str
    source_version: str | None
    website: str | None
    city: str | None
    checked_by: str | None       # employee uuid, required for human-lookup authorities
    region: str | None = None    # state / province / UK nation, used for grouping
    courses: tuple = ()          # approved courses as listed by the regulator
    phone: str | None = None     # phone printed in the register (regulator-published)

    @property
    def website_domain(self) -> str | None:
        if not self.website:
            return None
        h = (urlsplit(self.website if "://" in self.website else "https://" + self.website).hostname or "").lower()
        return h[4:] if h.startswith("www.") else (h or None)

    @property
    def approval_status(self) -> str:
        return "verified_by_human" if self.authority in HUMAN_LOOKUP else "listed_in_register"


def validate_row(raw: dict) -> RegisterRow:
    row = {k: (raw.get(k) or "").strip() or None for k in
           ("name", "country", "authority", "classification", "approval_ref", "source_url",
            "source_version", "website", "city", "checked_by", "region", "phone")}
    courses = tuple(c.strip() for c in (raw.get("courses") or "").split("|") if c.strip())
    missing = [k for k in REQUIRED if not row[k]]
    if missing:
        raise RegisterRowError(f"missing:{','.join(missing)}")
    row["country"] = row["country"].upper()
    if len(row["country"]) != 2:
        raise RegisterRowError("country_must_be_iso2")
    allowed = AUTHORITY_CLASSES.get(row["authority"])
    if allowed is None:
        raise RegisterRowError(f"unknown_authority:{row['authority']}")
    if row["classification"] not in allowed:
        raise RegisterRowError(f"classification_{row['classification']}_invalid_for_{row['authority']}")
    if not row["source_url"].startswith("https://"):
        raise RegisterRowError("source_url_must_be_https")
    if row["authority"] in HUMAN_LOOKUP and not row["checked_by"]:
        raise RegisterRowError("human_lookup_requires_checked_by")
    if row["authority"] in ("CASA", "FAA", "DGCA", "UK_CAA", "CRICOS") and not row["approval_ref"] \
            and row["classification"] != "PART61_OPERATOR_UNVERIFIED":
        raise RegisterRowError("approval_ref_required_for_dedupe")
    return RegisterRow(**row, courses=courses)


def read_csv(path: str) -> tuple[list[RegisterRow], list[tuple[int, str]]]:
    ok, bad = [], []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, raw in enumerate(csv.DictReader(fh), start=2):
            try:
                ok.append(validate_row(raw))
            except RegisterRowError as exc:
                bad.append((i, str(exc)))
    return ok, bad


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
