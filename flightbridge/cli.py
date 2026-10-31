"""FlightBridge CLI. Defaults are safe: DRY_RUN=true, OUTBOUND_ENABLED=false.

  python -m flightbridge.cli migrate
  python -m flightbridge.cli import-register rows.csv
  python -m flightbridge.cli research <org_id>
  python -m flightbridge.cli qualify <org_id> [--cpl] [--intl] [--fleet]
  python -m flightbridge.cli draft <org_id>
  python -m flightbridge.cli process --limit 20
  python -m flightbridge.cli status
"""
from __future__ import annotations

import argparse
import json
import sys

from . import db
from .compliance.rules import load_rules
from .research import pipeline
from .research.fetch import SafeFetcher
from .research.registers import read_csv
from .sending.service import SendingService
from .settings import Settings, load_sender_profile


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="flightbridge")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    ir = sub.add_parser("import-register"); ir.add_argument("csv")
    rs = sub.add_parser("research"); rs.add_argument("org_id"); rs.add_argument("--max-pages", type=int, default=12)
    q = sub.add_parser("qualify"); q.add_argument("org_id")
    for flag in ("--cpl", "--intl", "--fleet"):
        q.add_argument(flag, action="store_true")
    d = sub.add_parser("draft"); d.add_argument("org_id")
    pr = sub.add_parser("process"); pr.add_argument("--limit", type=int, default=20)
    sub.add_parser("status")
    a = p.parse_args(argv)

    s = Settings.from_env()
    if not s.database_url:
        print("FLIGHTBRIDGE_DATABASE_URL / DATABASE_URL not set", file=sys.stderr)
        return 2
    print(f"[flightbridge] DRY_RUN={s.dry_run} OUTBOUND_ENABLED={s.outbound_enabled}", file=sys.stderr)

    if a.cmd == "migrate":
        out = db.migrate(s.database_url)
    elif a.cmd == "import-register":
        rows, bad = read_csv(a.csv)
        out = {"imported": pipeline.import_rows(s.database_url, rows), "rejected_rows": bad}
    elif a.cmd == "research":
        out = pipeline.research_org(s.database_url, a.org_id, SafeFetcher(s.crawler_user_agent), a.max_pages)
    elif a.cmd == "qualify":
        extra = {"has_cpl": a.cpl, "intl_students": a.intl, "fleet_published": a.fleet}
        out = pipeline.qualify_org(s.database_url, a.org_id, {k: v for k, v in extra.items() if v})
    elif a.cmd == "draft":
        out = {"approval_task_id": pipeline.draft_initial(s.database_url, a.org_id)}
    elif a.cmd == "process":
        svc = SendingService(s, load_rules(), load_sender_profile())
        out = [o.__dict__ for o in svc.process_ready(a.limit)]
    else:
        with db.tx(s.database_url) as conn:
            out = {k: dict(conn.execute(sql).fetchall()) for k, sql in {
                "organisations": "SELECT status, count(*) FROM flightbridge.organisation GROUP BY 1",
                "approvals": "SELECT status, count(*) FROM flightbridge.approval_task GROUP BY 1",
                "outbound": "SELECT status, count(*) FROM flightbridge.outbound_message GROUP BY 1",
            }.items()}
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
