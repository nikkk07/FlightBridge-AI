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
| `config/jurisdictions.json` | Legal conditions, evidence policies, content requirements per country |
| `config/sender_profile.json` | Footer values; nulls block sending where required |
| `db/migrations/001_flightbridge_schema.sql` | Canonical schema (copied to CRM as 037; 002 as 038) |
| `flightbridge/compliance/` | Gate 1 jurisdiction, gate 2 content, suppression |
| `flightbridge/sending/` | The only sending service and transports |
| `flightbridge/research/` | Safe fetcher, crawler, extractor, registers, qualification, pipeline |
| `scripts/probe_hostinger.py` | Read-only Hostinger API probe |
| `scripts/check_crm_copies.py` | Verifies the CRM's copied migrations/rules have not drifted |
| `docs/OPERATIONS_RUNBOOK.md` | How the cycle runs, CRM controls, go-live steps, incident actions |
| `flightbridge/inbound/`, `flightbridge/ai/`, `flightbridge/followups.py`, `flightbridge/worker.py` | Inbox, classification, drafting, follow-ups, worker cycle |

Tests: start a Postgres, then `FB_TEST_ADMIN_URL=postgresql://postgres@/postgres?host=/tmp&port=55432 python3 -m pytest -q`. DB tests skip without Postgres.

Requirements: Python 3.11+, `psycopg[binary]`, `requests`, `pytest`.

## Web app (`web/`)

Standalone FlightBridge workspace (Vite + React), live at https://flying.weoneaviation.in once DNS is added. It signs in with CRM accounts and calls the CRM backend (`/api/auth/login`, `/api/flightbridge/*`), so it shares data with the CRM's Partnerships tab. Roles: CRM Admin department or `permissions.flightbridge = "admin"` → admin; `"researcher"` → can verify schools and record evidence, cannot approve emails, templates or sending.

Local: `cd web && npm install && VITE_API_URL=http://localhost:8000 npm run dev`. Vercel: root directory `web`, env `VITE_API_URL`.
