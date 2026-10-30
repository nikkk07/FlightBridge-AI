# FlightBridge AI

Flying-school partnership intelligence and outreach platform for We One Aviation.

**Status:** Phase 1b (research) is complete. No email sending exists or is enabled.

| Path | Content |
|---|---|
| `docs/PHASE_1B_RESEARCH_REPORT.md` | Findings with primary source, exact clause, verification date and implication |
| `docs/TECHNICAL_FEASIBILITY_MATRIX.md` | Capability-by-capability feasibility and cost floor |
| `docs/RESEARCH_AND_SOURCE_REGISTER.md` | Source register (S01–S35, O1–O2) |
| `docs/COMPANY_FACTS_RECONCILIATION.md` | Website contradictions and the approved-for-outreach fact set |
| `config/jurisdictions.json` | Machine-readable email eligibility rules |
| `flightbridge/compliance/eligibility.py` | Eligibility + suppression gate (pure Python, no dependencies) |
| `tests/` | `python3 -m pytest -q` |

Integration target: the existing `nikkk07/CRM` stack (Vercel frontend, Render FastAPI backend, Supabase Postgres).
