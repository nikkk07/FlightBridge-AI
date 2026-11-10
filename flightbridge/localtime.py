"""A school's own clock: time zone from its country (and state/province where a country spans several zones),
and whether it is a weekday business hour there. Used to time first emails and follow-ups."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

COUNTRY_TZ = {
    "US": "America/Chicago", "CA": "America/Toronto", "MX": "America/Mexico_City", "BR": "America/Sao_Paulo",
    "AR": "America/Argentina/Buenos_Aires", "CL": "America/Santiago", "CO": "America/Bogota", "PE": "America/Lima",
    "GB": "Europe/London", "IE": "Europe/Dublin", "PT": "Europe/Lisbon", "ES": "Europe/Madrid", "FR": "Europe/Paris",
    "BE": "Europe/Brussels", "NL": "Europe/Amsterdam", "LU": "Europe/Luxembourg", "DE": "Europe/Berlin",
    "CH": "Europe/Zurich", "AT": "Europe/Vienna", "IT": "Europe/Rome", "MT": "Europe/Malta", "DK": "Europe/Copenhagen",
    "NO": "Europe/Oslo", "SE": "Europe/Stockholm", "FI": "Europe/Helsinki", "EE": "Europe/Tallinn", "LV": "Europe/Riga",
    "LT": "Europe/Vilnius", "PL": "Europe/Warsaw", "CZ": "Europe/Prague", "SK": "Europe/Bratislava",
    "HU": "Europe/Budapest", "SI": "Europe/Ljubljana", "HR": "Europe/Zagreb", "RS": "Europe/Belgrade",
    "RO": "Europe/Bucharest", "BG": "Europe/Sofia", "GR": "Europe/Athens", "CY": "Asia/Nicosia", "TR": "Europe/Istanbul",
    "UA": "Europe/Kyiv", "IS": "Atlantic/Reykjavik",
    "ZA": "Africa/Johannesburg", "KE": "Africa/Nairobi", "NG": "Africa/Lagos", "EG": "Africa/Cairo", "MA": "Africa/Casablanca",
    "ET": "Africa/Addis_Ababa", "GH": "Africa/Accra", "NA": "Africa/Windhoek", "BW": "Africa/Gaborone",
    "AE": "Asia/Dubai", "SA": "Asia/Riyadh", "QA": "Asia/Qatar", "BH": "Asia/Bahrain", "KW": "Asia/Kuwait",
    "OM": "Asia/Muscat", "JO": "Asia/Amman", "IL": "Asia/Jerusalem",
    "IN": "Asia/Kolkata", "NP": "Asia/Kathmandu", "LK": "Asia/Colombo", "BD": "Asia/Dhaka", "PK": "Asia/Karachi",
    "TH": "Asia/Bangkok", "VN": "Asia/Ho_Chi_Minh", "MY": "Asia/Kuala_Lumpur", "SG": "Asia/Singapore",
    "ID": "Asia/Jakarta", "PH": "Asia/Manila", "HK": "Asia/Hong_Kong", "TW": "Asia/Taipei", "CN": "Asia/Shanghai",
    "KR": "Asia/Seoul", "JP": "Asia/Tokyo",
    "AU": "Australia/Sydney", "NZ": "Pacific/Auckland", "FJ": "Pacific/Fiji",
}

US_STATE_TZ = {
    **dict.fromkeys(["CT", "DE", "DC", "FL", "GA", "IN", "KY", "ME", "MD", "MA", "MI", "NH", "NJ", "NY", "NC", "OH", "PA",
                     "RI", "SC", "VT", "VA", "WV"], "America/New_York"),
    **dict.fromkeys(["AL", "AR", "IL", "IA", "KS", "LA", "MN", "MS", "MO", "NE", "ND", "OK", "SD", "TN", "TX", "WI"],
                    "America/Chicago"),
    **dict.fromkeys(["CO", "ID", "MT", "NM", "UT", "WY"], "America/Denver"),
    "AZ": "America/Phoenix", **dict.fromkeys(["CA", "NV", "OR", "WA"], "America/Los_Angeles"),
    "AK": "America/Anchorage", "HI": "Pacific/Honolulu", "PR": "America/Puerto_Rico",
}
CA_PROV_TZ = {"BC": "America/Vancouver", "AB": "America/Edmonton", "SK": "America/Regina", "MB": "America/Winnipeg",
              "ON": "America/Toronto", "QC": "America/Toronto", "NB": "America/Halifax", "NS": "America/Halifax",
              "PE": "America/Halifax", "NL": "America/St_Johns", "YT": "America/Whitehorse", "NT": "America/Yellowknife",
              "NU": "America/Iqaluit"}
AU_STATE_TZ = {"NSW": "Australia/Sydney", "ACT": "Australia/Sydney", "VIC": "Australia/Melbourne",
               "TAS": "Australia/Hobart", "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide",
               "NT": "Australia/Darwin", "WA": "Australia/Perth"}
REGION_TZ = {"US": US_STATE_TZ, "CA": CA_PROV_TZ, "AU": AU_STATE_TZ}
# full names some sources use instead of codes
_NAMES = {"new south wales": "NSW", "victoria": "VIC", "queensland": "QLD", "western australia": "WA",
          "south australia": "SA", "tasmania": "TAS", "northern territory": "NT", "australian capital territory": "ACT",
          "british columbia": "BC", "alberta": "AB", "ontario": "ON", "quebec": "QC", "manitoba": "MB",
          "saskatchewan": "SK", "nova scotia": "NS", "new brunswick": "NB", "california": "CA", "florida": "FL",
          "texas": "TX", "arizona": "AZ", "washington": "WA", "oregon": "OR", "new york": "NY", "colorado": "CO",
          "newfoundland and labrador": "NL", "prince edward island": "PE", "yukon": "YT", "northwest territories": "NT",
          "nunavut": "NU",
          # every US state (full name -> code); before 1.8.4 most fell back to Central time
          "alabama": "AL", "alaska": "AK", "arkansas": "AR", "connecticut": "CT", "delaware": "DE",
          "district of columbia": "DC", "washington dc": "DC", "washington, d.c.": "DC", "georgia": "GA", "hawaii": "HI",
          "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
          "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
          "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
          "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "north carolina": "NC", "north dakota": "ND",
          "ohio": "OH", "oklahoma": "OK", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
          "south dakota": "SD", "tennessee": "TN", "utah": "UT", "vermont": "VT", "virginia": "VA",
          "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR"}


def school_tz(country: str | None, region: str | None = None) -> ZoneInfo | None:
    """None when the country isn't known: the caller must not guess a business hour."""
    c = (country or "").upper()
    if region and c in REGION_TZ:
        r = region.strip()
        r = _NAMES.get(r.lower(), r).upper()
        if r in REGION_TZ[c]:
            return ZoneInfo(REGION_TZ[c][r])
    name = COUNTRY_TZ.get(c)
    return ZoneInfo(name) if name else None


def in_business_hours(now: datetime, country: str | None, region: str | None = None, start_h: int = 9,
                      end_h: int = 17) -> bool:
    tz = school_tz(country, region)
    if tz is None:
        return False
    local = now.astimezone(tz)
    return local.weekday() < 5 and start_h <= local.hour < end_h


def business_time(base: datetime, days: int, country: str | None, start_h: int = 9, end_h: int = 17,
                  region: str | None = None) -> datetime:
    """`days` after base, moved into the next weekday business hour at the school (UTC if the country is unknown)."""
    tz = school_tz(country, region) or ZoneInfo("UTC")
    local = (base + timedelta(days=days)).astimezone(tz)
    if local.hour < start_h:
        local = local.replace(hour=start_h, minute=0, second=0, microsecond=0)
    elif local.hour >= end_h:
        local = (local + timedelta(days=1)).replace(hour=start_h, minute=0, second=0, microsecond=0)
    while local.weekday() >= 5:
        local = (local + timedelta(days=1)).replace(hour=start_h, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)
