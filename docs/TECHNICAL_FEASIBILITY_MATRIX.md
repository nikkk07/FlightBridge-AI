# Technical Feasibility Matrix — v2 (2026-10-31)

Legend: ✅ feasible now · 🟡 feasible with a condition · 🔴 not feasible as specified · ⏸ blocked on your decision. Evidence IDs refer to `PHASE_1B_RESEARCH_REPORT.md`.

| # | Capability | Status | Method | Condition / limitation | Evidence |
|---|---|---|---|---|---|
| 1 | Discover Indian FTOs | ✅ | Parse the DGCA "Ranking of FTOs" PDF; reconcile the count to MoCA (41 FTOs / 63 bases) | Dedupe per FTO, not per base | F4 |
| 2 | Discover UK schools | ✅ | Parse CAA SD31 (ATO) and SD30 (DTO) PDFs; monthly version check | Classify DTOs as PPL-level only; read CPL scope per ATO entry | F4 |
| 3 | Discover EU schools | 🟡 | Per-state NCA register adapters | The EASA list is not a complete directory; each state needs its own source | F4 |
| 4 | Discover US / CA / AU schools | 🟡 | Human-assisted lookups (FAA dashboard, TC FTU search, CASA) | No export/API; CASA robots-disallowed; dedupe on certificate/ARN | F4 |
| 5 | Find school websites via Google Places | 🟡 | Text Search (Pro mask), then Details Enterprise only for unmatched schools | Persist `place_id` only; seeding a crawl from a Places URL needs legal review; India pricing eligibility uncertain | F3 |
| 6 | Get emails from Google Places | 🔴 | — | No email field exists in any SKU | F3 |
| 7 | Extract public emails from school websites | ✅ | robots-aware crawler, contact/admissions pages, source URL + timestamp per address | Never guess addresses | F8 |
| 8 | Confirm mailbox existence | 🔴 | — | Syntax, MX and SMTP checks only. "Confirmed" comes only from delivery/bounce outcome | Phase 1 |
| 9 | Regulatory status verification | 🟡 | Automated for IN/UK PDFs; human-confirmed for US/CA/AU | Status stored with source URL and check date; revalidate monthly | F4 |
| 10 | Jurisdiction gate + content-readiness gate + 3-scope suppression | ✅ **implemented** | `compliance/jurisdiction.py`, `compliance/content.py`, `compliance/suppression.py`, DB trigger `outbound_guard` | Legal conditions need unexpired evidence of an allowed method; every content requirement is validated against the exact body; parametrized tests prove each missing condition/requirement blocks | F5 |
| 11 | Send / reply via Hostinger Mail API | ⏸ | REST `POST /api/v1/mailboxes/{id}/send`, `inReplyTo` | Separate paid product; MX of both domains is **not** Hostinger; needs a dedicated mailbox or subdomain | F1, F2 |
| 12 | Inbox monitoring | 🟡 | `message.received` webhook (Bearer-secret auth) as a hint, then authenticated re-fetch; polling reconciliation | API models expose no thread id; local threading from Message-ID/In-Reply-To + Ref token unless a live probe proves otherwise | F2 |
| 13 | IMAP/SMTP fallback | 🟡 | `imap.hostinger.com:993` / `smtp.hostinger.com:465` for a Hostinger box; otherwise the current host's settings | Current .in mail host is `host2.dnsinweb.com`, limits unknown | F1, F2 |
| 14 | Domain authentication | 🟡 | Fix duplicate DMARC on .in (RFC 9989 discards both); subdomain only after Hostinger confirms support | Subdomain = risk reduction, not isolation guarantee; no DNS changed | F1 |
| 15 | CRM integration | ✅ **implemented** | Migrations 037–038 (schema `flightbridge`), `flightbridge_routes.py`, Partnerships tab | 9 CRM tests pass; existing routes verified; frontend builds | F7 |
| 16 | Durable jobs / recovery | 🟡 | Postgres job table with leases + idempotency keys | Render free tier spins down and is "not for production"; paid instance or external scheduler needed before sending | F7 |
| 17 | Backups | 🟡 | Supabase Pro ($25/mo) | Free plan has no automatic backups and pauses after 1 week idle | F7 |
| 18 | AI classification / drafting | ✅ | Existing Groq key or another provider; deterministic rules first | Needs an evaluation set before any GREEN auto-send | F7 |
| 19 | Grounded answers about We One | ⏸ | Versioned knowledge base | Only the 5 approved facts until you sign off | F6 |
| 20 | n8n orchestration | 🔴 (not justified) | — | No capability gap versus FastAPI; adds runtime, secrets and audit surface | F7 |
| 21 | External sending | ⏸ disabled | `DRY_RUN=true`, `OUTBOUND_ENABLED=false` defaults | Explicit activation by you only | F9 |

## Recurring cost floor (verified prices; volumes are assumptions)

| Item | Cost | Basis |
|---|---|---|
| Places, ≤5,000 Text Search Pro + ≤1,000 Details Enterprise per month | **$0** (global free caps) | F3 |
| Places above the free caps | $32 / $20 per 1,000 (global); $9.60 / $6.00 (India, if eligible) | F3 |
| Hostinger Mail API mailbox | $0.39–$1.99/mo prepaid 48 months (renews $1.59–$3.99) | F2 |
| Supabase Pro (recommended before activation) | $25/mo | F7 |
| Render always-on instance | price not verified this session | F7 |
| LLM | to be computed in Phase 2 from the measured token volume | — |
