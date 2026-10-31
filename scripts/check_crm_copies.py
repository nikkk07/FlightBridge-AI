"""Verify the CRM's copies of FlightBridge code/SQL have not drifted.
Usage: python3 scripts/check_crm_copies.py /path/to/CRM   (the folder containing backend/)"""
import hashlib
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
crm = pathlib.Path(sys.argv[1]) / "backend"
ok = True
for canon, copy in (("001_flightbridge_schema.sql", "036_flightbridge_schema.sql"),
                    ("002_templates_inbound_followups.sql", "037_flightbridge_templates_inbound.sql")):
    h = hashlib.sha256((ROOT / "db" / "migrations" / canon).read_bytes()).hexdigest()
    text = (crm / "migrations" / copy).read_text()
    body_same = text.split("\n", 5)[5] == (ROOT / "db" / "migrations" / canon).read_text()
    print(f"{copy}: hash-in-header={'yes' if h in text else 'NO'} body-identical={'yes' if body_same else 'NO'}")
    ok &= (h in text) and body_same
from flightbridge.compliance.content import BANNED_CLAIMS
from flightbridge.outreach.templates import validate
spec = importlib.util.spec_from_file_location("crm_rules", crm / "flightbridge_template_rules.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
same = m.BANNED_CLAIMS == BANNED_CLAIMS and all(validate(s, b) == m.validate(s, b) for s, b in [
    ("Hi {{school_name}}", "Dear {{school_name}} team, we teach DGCA theory."), ("Re: x", "3000+ pilots, our fleet, TODO {{bad}}")])
print(f"flightbridge_template_rules.py: identical={'yes' if same else 'NO'}")
sys.exit(0 if ok and same else 1)
