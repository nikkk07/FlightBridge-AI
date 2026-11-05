# FlightBridge AI

Flying-school partnership intelligence and outreach platform for We One Aviation.

**Status:** Phases 2–7 implemented and tested in dry-run. Defaults: `DRY_RUN=true`, `OUTBOUND_ENABLED=false`, DB kill switch engaged. No email has been sent.

| Path | Content |
|---|---|
| `docs/PHASE_2_ARCHITECTURE.md` | Corrections 1–10 with evidence, architecture, state machine, schema, CRM module, open items |
| `docs/PHASE_1B_RESEARCH_REPORT.md` | Research findings (revised 2026-10-31) |
| `docs/TECHNICAL_FEASIBILITY_MATRIX.md` | Capability feasibility (v2) |
| `docs/RESEARCH_AND_SOURCE_REGISTER.md` | Source register |
| `docs/COMPANY_FACTS_RECONCILIATION.md` | Approved-for-outreach fact set |
| `flightbridge/config/jurisdictions.json` | Legal conditions, evidence policies, content requirements per country |
| `flightbridge/config/sender_profile.json` | Footer values; nulls block sending where required |
| `flightbridge/migrations/*.sql` | Canonical schema 001-005 (copied to CRM as 037-041); the engine applies them itself on start-up (`db.ensure_schema`) |
| `flightbridge/compliance/` | Gate 1 jurisdiction, gate 2 content, suppression |
| `flightbridge/sending/` | The only sending service and transports |
| `flightbridge/research/` | Safe fetcher, crawler, extractor, registers, qualification, pipeline |
| `scripts/probe_hostinger.py` | Read-only Hostinger API probe |
| `scripts/check_crm_copies.py` | Verifies the CRM's copied migrations/rules have not drifted |
| `docs/OPERATIONS_RUNBOOK.md` | How the cycle runs, CRM controls, go-live steps, incident actions |
| `flightbridge/inbound/`, `flightbridge/ai/`, `flightbridge/followups.py`, `flightbridge/worker.py` | Inbox, classification, drafting, follow-ups, worker cycle |

Tests: start a Postgres, then `FB_TEST_ADMIN_URL=postgresql://postgres@/postgres?host=/tmp&port=55432 python3 -m pytest -q`. DB tests skip without Postgres.

Requirements: Python 3.11+, `psycopg[binary]`, `requests`, `pytest`.

## Where to use it

FlightBridge AI lives inside the CRM (FlightBridge AI tab): Schools by Country › State › Area with Eligible / Check needed / Unqualified sections, Areas, Automation, Approvals, Templates, Inbox, Team and Settings. The engine runs in the CRM server's scheduler. Roles: CRM Admin department or `permissions.flightbridge = "admin"` → admin; `"researcher"` → can verify schools and record evidence, cannot approve emails, templates or sending.

## How it runs (automation)

The worker (`flightbridge/worker.py` + `flightbridge/automation.py`) is the engine. Each cycle: Run-now requests from the app, then find websites (Google Places, confirmed on the school's own homepage), read the sites (public emails + signals, optional free-LLM summary via Groq or Gemini), score the fit, draft first emails from the approved template into Approvals, then replies, follow-ups and the send queue (still dry-run).

- Normal setup: the CRM backend installs this package (`requirements.txt`, pinned commit) and runs the engine in its scheduler every 15 minutes (`CRM/backend/flightbridge_engine.py`). API keys are pasted in FlightBridge Settings (CRM tab or Sending & safety -> Connections), checked live, stored encrypted (Fernet, key from `FB_SECRETS_KEY` or the CRM's `SECRET_KEY`). Environment variables still override.
- Backup runner: `.github/workflows/flightbridge-worker.yml` (manual). The engine lock (`flightbridge.engine_lease`) allows one cycle at a time anywhere.
- World sweep (`flightbridge/research/sweep.py`): finds flying schools country by country across the whole world, main training markets first (US, CA, AU, NZ, GB, IE, ZA, then Europe, Asia, Middle East, Latin America, Africa, then everyone else). The map is cut into 1-degree squares that hold an airfield (`config/world_cells.json`, from OurAirports, public domain; rebuild with `scripts/build_world_cells.py`); each square gets one Places Text Search "flight school" restricted to the square, up to 60 results, and a full square is split in four. Text Search Pro fields only (no website or phone). Starts again every 180 days. Every school seen is logged with its position (`flightbridge.detection`) for the CRM's live globe; older schools with a Google id are placed with Place Details (location only).
- Google free allowance (`flightbridge/research/quota.py`): every Places call is counted per SKU in `flightbridge.google_usage` before it is made and refused at 90% of Google's free monthly cap (Text Search Pro 4,500, Text Search Enterprise 900, Place Details Pro 4,500, Place Details Enterprise 900), shared by every runner. The sweep, website search and Google phone lookups are paced to spread each allowance evenly over the month. `system_setting 'google_budget'` can lower a cap, never raise it past the free tier.
- Websites without Google (`flightbridge/research/webfind.py`): each school is tried cheapest first. 1) Guess: likely addresses built from the name (e.g. shebleaviation.com, sheble.aero), DNS-checked, homepage read; free and unlimited (25 schools a cycle). 2) Tavily web search (https://docs.tavily.com, 1,000 free credits a month, 1 per basic search), counted as `tavily_search` in `flightbridge.google_usage` and refused at 900. Key: FlightBridge AI > Settings, or `TAVILY_API_KEY`. 3) Google Places website look-up. A page is accepted only after the engine reads it: it names the school, is about flying, is not parked, and shows the school's town (or 2+ name words and its state, or 3+ name words, or its approval number). Each method is retried for a school after 30 days.
- Re-verify (`automation.recheck`): websites last read more than 90 days ago are read again (oldest first); the new evidence re-categorises the school.
- Official lists uploaded in the CRM (`flightbridge.register_upload`, `research/faa_upload.py`): the FAA publishes Part 141 pilot schools only through its facility dashboard (https://www.faa.gov/av-info/facility-dashboard), which refuses automated downloads, so a person downloads the list (CSV or Excel) and uploads it in FlightBridge AI > Settings. The engine imports it on its next cycle: matched by certificate number or by name and state, new schools added, and matching US schools become Eligible (listed in the FAA register). The stored file is cleared after import.
- Countries without an approval register: once the school's website has been read it goes to "Check needed"; a person confirms it on the national aviation authority's site (recorded as `NATIONAL_APPROVAL`, the page they checked is required) before it can be drafted for outreach.
- Registers: every `flightbridge/registers/**/*_register.csv` is imported automatically by the worker when its content changes (sha256-guarded, so regulator check dates are never refreshed without a new file).
- Office Mac (optional, faster Run-now): `scripts/mac/install_launchd.sh` installs a LaunchAgent that runs the worker while the Mac is on.
- Instant Run-now (optional): set `FB_GITHUB_DISPATCH_TOKEN` (fine-grained token, Actions: write on this repo) on the CRM server; Run-now then starts the GitHub worker immediately.
