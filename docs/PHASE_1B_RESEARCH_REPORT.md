# FlightBridge AI — Phase 1b Research Report

Verification date for every finding: **2026-10-30** (IST). No email was sent and no external outreach was activated.
Grades: **V** = verified on an official primary source this session · **P** = partially verified (primary page fetched but the exact clause was not retrievable) · **S** = secondary source only · **O** = direct observation (DNS query, repo inspection).

---

## F1. Our own email infrastructure is not on Hostinger today (blocker) — O

| Item | Observed (Cloudflare DoH, 2026-10-30 06:46 UTC) |
|---|---|
| weoneaviation.in NS | `ns11/ns12.dnshorizon.com` |
| weoneaviation.in MX | `0 host2.dnsinweb.com` (A 103.235.104.210) |
| weoneaviation.in SPF | `v=spf1 +a +mx +ip4:103.235.104.210 +ip4:103.235.104.211 ~all` |
| weoneaviation.in DMARC | **two records**: `p=none` and `p=reject; rua=mailto:info.weoneaviation@gmail.com` |
| weoneaviation.in DKIM | `default._domainkey` present (RSA-2048) |
| weoneaviation.com NS | `ns1/ns2.techvyapar.in` |
| weoneaviation.com MX | `0 weoneaviation.com` (self-hosted, A 129.121.97.39) |
| weoneaviation.com SPF / DMARC | `+a +mx +ip4:129.121.97.39 ~all` / `p=none` |

**Implications**
- The brief says the mailbox is on Hostinger. Neither domain's MX record points to Hostinger, so a Hostinger mailbox on these domains would not receive mail today. Hostinger Mail API cannot read the live inbox unless MX is changed.
- With two DMARC records on .in, DMARC is effectively not applied. RFC 7489 §6.6.3 says policy discovery stops when more than one record is found ([RFC 7489](https://www.rfc-editor.org/rfc/rfc7489#section-6.6.3)). Fix: keep one record. Do not change it until the owner of `dnshorizon` DNS confirms which mail sources are legitimate.
- **Recommendation:** use a dedicated outreach subdomain, for example `partners.weoneaviation.in`, with its own MX, SPF, DKIM and DMARC. This keeps the main domain's reputation and existing mail flow safe, and lets a Hostinger Mail API mailbox exist without moving the main domain's MX. This depends on your decision.

## F2. Hostinger Mail API is a separately priced, per-mailbox product — V

| Fact | Source and exact text | Implication |
|---|---|---|
| Sold per mailbox: Starter $0.39/mo (48-month prepay, renews $1.59), 1,000 emails/day; Standard and Premium 3,000/day | [hostinger.com/mail-api](https://www.hostinger.com/mail-api): "Price per mailbox… **1 000** emails/day per mailbox" | Budget it as a new line item. Assume it is **not** included in any existing plan until hPanel shows "Agentic Mail → API access" for your order. |
| "Built for product and workflow email rather than high-volume bulk delivery" | same page, FAQ | Fits low-volume, personalised B2B outreach. Not a bulk tool. |
| Tokens are created under **Agentic Mail → API access** and scoped to one order, optionally to specific mailboxes | [docs.hostinger.com/api-reference/email-sdks](https://docs.hostinger.com/api-reference/email-sdks) (updated 2026-10-27) | Least-privilege token per mailbox. Store as `HOSTINGER_MAIL_API_TOKEN` in Render env, never in git. |
| 27 operations. Send is `POST /api/v1/mailboxes/{mailboxResourceId}/send`; replies use `inReplyTo` | same | Reply threading works through standard headers. |
| SDK v1.18.2 (2026-10-09) has **no thread methods**; it has search_messages, get_message_source, webhooks and quota | [pypi.org/project/hostinger-mail-api](https://pypi.org/project/hostinger-mail-api/) | We build our own threading from `Message-ID`, `In-Reply-To` and `References`, stored in Supabase. |
| Webhooks have a one-time secret. The **signature header and algorithm are undocumented** | [docs.hostinger.com/emails/agentic-mail](https://docs.hostinger.com/emails/agentic-mail); WebhooksApi.md | A webhook is only a "new mail" hint. Each message is re-fetched by ID over the authenticated API before any action. Polling reconciliation runs every N minutes. |
| `429` means back off. No numeric API rate limit is published | email-sdks page | Exponential backoff and a client-side token bucket. |
| IMAP `imap.hostinger.com:993` and SMTP `smtp.hostinger.com:465`, both SSL | [docs.hostinger.com/emails/setup-devices](https://docs.hostinger.com/emails/setup-devices) (updated 2026-07-22) | Fallback path. Only valid for mailboxes hosted at Hostinger (see F1). |
| Business Email limits apply per mailbox: 100 recipients per message; 1,000/day Starter, 3,000/day Premium, 100/day free and trial | [Hostinger support: parameters and limits](https://www.hostinger.com/support/4625828-how-to-create-and-manage-hostinger-email/) | Our sending cap is far below these limits (see F9). |

## F3. Google Places API (New): cost, fields and terms — V

**Fields.** `websiteUri`, `nationalPhoneNumber` and `internationalPhoneNumber` are **Enterprise** fields. `id` is Essentials IDs Only. `displayName`, `formattedAddress`, `businessStatus` and `location` are **Pro** ([Text Search docs](https://developers.google.com/maps/documentation/places/web-service/text-search), updated 2026-10-28). There is **no email field** in any SKU.

**Pricing (USD per 1,000 events, first tier / free monthly cap)**

| SKU | Global ([pricing](https://developers.google.com/maps/billing-and-pricing/pricing)) | India ([pricing-india](https://developers.google.com/maps/billing-and-pricing/pricing-india)) |
|---|---|---|
| Text Search Essentials (IDs Only) | unlimited free | unlimited free |
| Text Search Pro | $32.00 / 5,000 | $9.60 / 35,000 |
| Text Search Enterprise | $35.00 / 1,000 | $10.50 / 7,000 |
| Place Details Essentials | $5.00 / 10,000 | $1.50 / 70,000 |
| Place Details Pro | $17.00 / 5,000 | $5.10 / 35,000 |
| Place Details Enterprise | $20.00 / 1,000 | $6.00 / 7,000 |

**India eligibility risk.** The India price list is "only available to India-based customers that incur a large majority of usage in India", and Google monitors eligibility ([India FAQ](https://developers.google.com/maps/billing-and-pricing/india)). Our searches target schools abroad, so qualifying is **uncertain**. Budget on global prices and treat India pricing as upside only.

**Terms (blocking constraints)**
- Maps Platform ToS §3.2.3(a)(iii): Customer will not "copy and save business names, addresses, or user reviews". §3.2.3(d)(iii) bars use "in a listings or directory service" ([ToS](https://cloud.google.com/maps-platform/terms), modified 2026-08-26).
- Service Terms A.3: `place_id` may be cached. B.14.3: lat/lng may be cached for **30 days** at most ([Service Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms), modified 2026-10-28).
- Places policies: attribution is required. Without a Google map, the Google logo is required ([policies](https://developers.google.com/maps/documentation/places/web-service/policies)).

**Implications (design rule):**
1. Regulator registers and school websites are the **system of record**. Places is a transient discovery aid only.
2. Persist only `place_id`. Never persist a Places-returned name, address, phone or website. Website facts are re-derived by fetching the school's own site and stored with that site as the source.
3. Call flow to minimise Enterprise calls: (a) Text Search with a **Pro** field mask (`places.id,places.displayName,places.formattedAddress,places.businessStatus`). (b) Match against the regulator list in memory. (c) Call **Place Details Enterprise (`websiteUri`) only** for regulator-listed schools with no known website. (d) Discard the response after the crawl seed is verified. Never request Atmosphere fields.
4. Whether a Places-sourced URL may seed a crawl, even if not stored, is a ToS interpretation. **Legal review is recommended before Places is enabled in production.**
5. OAuth is not needed. Places uses a server-side API key restricted to the Places API and to the backend's egress.

## F4. Aviation regulators: classification and verifiability

| Jurisdiction | Correct classification | Official source (verified 2026-10-30) | Machine retrieval | Implication |
|---|---|---|---|---|
| India | **FTO**, DGCA-approved | MoCA: **41 FTOs, 63 operational bases, "Till 02 July 2026"** ([civilaviation.gov.in/node/4635](https://www.civilaviation.gov.in/node/4635), updated 27/10/2026) — V. The DGCA list is the "Ranking of FTOs – April 2026" PDF ([dgca.gov.in PDF](https://www.dgca.gov.in/digigov-portal/jsp/dgca/topHeader/aboutDGCA/Ranking%20of%20FTOs-April%202026.pdf)) | PDF parse. Count comes from MoCA. The DGCA ranking PDF is the name list | The count is published by **MoCA** (DGCA's parent ministry), not on the DGCA page. Seed is 41 entities. **Dedupe on FTO, not base** (63 bases ≠ 63 schools). |
| UK | **ATO** (Part-ORA, UK Aircrew Reg.) vs **DTO** (Annex VIII) | ATO list: **Standards Document 31, v221, 30-Sep-2026, PDF** ([caa.co.uk/publication/pid/2854](https://www.caa.co.uk/publication/pid/2854)). DTO list: Standards Document 30 ([CAA lists page](https://www.caa.co.uk/data-and-analysis/approved-persons-and-organisations/approved-organisations/pilot-training-providers-atos-and-dtos/)) — V | PDF parse, monthly version check | A DTO is "registered with the UK CAA for the delivery of training towards the Private Pilot Licence" (CAA page). A non-complex ATO covers LAPL/PPL plus ratings, including IR ([CAA ATO page](https://www.caa.co.uk/general-aviation/pilot-training-organisations/approved-training-organisations/)). **CPL/ATPL capability must be read from each ATO's approved-course entry. Never infer it from "ATO".** DTOs are classified as PPL-level only and are not CPL partners. |
| EU | **ATO** approved by a national competent authority, or **DTO** declared to it | EASA ATO page refers to its tasks for PTOs "located outside the territory of the Member States" ([easa.europa.eu ATO page](https://www.easa.europa.eu/easa-and-you/aircrew-and-medical/approved-training-organisations-ato)) — P. Reg. (EU) 2018/1139 articles not retrievable in full | Per-state registers | **EASA's oversight list is not a directory of European ATOs.** Most ATOs are overseen by national CAAs, so each target state needs its own register adapter. |
| USA | **Part 141 pilot school** (FAA Air Agency Certificate) vs Part 61 instruction (no school certificate) | FAA Part 141 page links the **Facility Dashboard** ([faa.gov pilotschools](https://www.faa.gov/licenses_certificates/airline_certification/pilotschools), updated 2026-06-03; [dashboard](https://www.faa.gov/av-info/facility-dashboard), updated 2026-08-11) — V | No documented export or API | Verification is a human-assisted dashboard lookup. Part 61 schools are labelled `unverified_school_status`, never "approved". |
| Canada | **Flight Training Unit (FTU)**, CARs 406 | TC "Flight Training Units Search" referenced ([tc.canada.ca](https://tc.canada.ca/en/aviation/licensing-pilots-personnel/getting-trained-pilot-crew-member/how-select-flying-school), modified 2019-04-29) — P | Search UI only. The page does not say it confirms certificate validity | Classification `TC_FTU_listed`. Certificate validity is a human check. |
| Australia | **Part 141 / Part 142 operator** (CASR) | CASA "Find a flight training operator" ([casa.gov.au search](https://www.casa.gov.au/search-centre/flight-training-operators)) — V. The full list page **disallows robots** | **No automated retrieval** (robots.txt) | Human lookup only. Results list operators per location, so **dedupe on CASA ARN/certificate**, not by search-hit count. |

**Classification enum (to implement):** `FTO_DGCA`, `ATO_UK`, `DTO_UK`, `ATO_EU_NCA`, `DTO_EU_NCA`, `PART141_FAA`, `PART61_OPERATOR_UNVERIFIED`, `FTU_TC`, `PART141_CASA`, `PART142_CASA`, `UNCLASSIFIED`, plus non-school types: `SIM_CENTRE`, `OPERATOR`, `CONSULTANCY`, `RECRUITER`.

## F5. Email law by jurisdiction — implemented as code

Implemented in `config/jurisdictions.json` and `flightbridge/compliance/eligibility.py`. Tests: `tests/test_eligibility.py`, **13/13 passing**.

| Country | Rule (source) | Gate result |
|---|---|---|
| US | CAN-SPAM: "no exception for business-to-business email". Requires postal address and opt-out, honoured within **10 business days**. Penalty up to **$53,088 per email** ([FTC guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)) — V | ALLOW, with required footer elements |
| Canada | CASL implied consent needs all three: conspicuous publication, no refusal statement, relevance to the recipient's role. Unsubscribe processed within **10 business days** and valid for **60 days**. Applies to mail sent into Canada from abroad ([CRTC FAQ](https://crtc.gc.ca/eng/com500/faq500.htm)) — V | ALLOW only if all three facts are verified and recorded with a source URL; otherwise REVIEW. A refusal statement means BLOCK |
| UK | PECR: corporate subscribers need no consent. Sole traders and non-Scottish partnerships do. UK GDPR applies to named employee addresses. Guidance under review after the Data (Use and Access) Act ([ICO](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/)) — V | Corporate role address: ALLOW. Named person: REVIEW (LIA and Art. 14 notice). Sole trader: BLOCK |
| Germany | UWG §7(2) no.2: e-mail advertising without "prior express consent" is an unacceptable nuisance, and it is not limited to consumers ([UWG English](https://www.gesetze-im-internet.de/englisch_uwg/englisch_uwg.html)) — V | BLOCK |
| Other EU | GDPR plus national ePrivacy rules. Treatment of legal persons is set nationally — P | REVIEW until each state is configured |
| Australia | Spam Act: consent required. Unsubscribe honoured within **5 working days** ([ACMA](https://www.acma.gov.au/avoid-sending-spam)) — V. Sched. 2 cl. 4 (conspicuous publication) text not retrieved — P | REVIEW |
| India | DPDP Rules 2025 notified 13 Nov 2025. Substantive obligations reported from **13 May 2027** ([AZB & Partners](https://www.azbpartners.com/bank/update-indias-digital-personal-data-protection-framework-comes-into-effect/)) — **S** (Gazette not retrieved) | REVIEW |
| Any other country | — | BLOCK |

**Suppression design:**
- Address and domain suppression are checked by SHA-256 of the normalised address.
- Opt-out takes effect on receipt. The hard SLA is **5 business days**, the strictest verified window (AU).
- Suppression records are never deleted. Records of "objection to direct marketing" are kept to honour GDPR Art. 21(3) (P).

## F6. Company facts must be reconciled before outreach — O

See `docs/COMPANY_FACTS_RECONCILIATION.md`. Until you sign it off, the knowledge base holds only the **Approved-for-outreach** set listed there. Everything else is blocked from templates and from AI replies.

## F7. Existing stack constraints (repo `nikkk07/CRM` inspected) — O + V

- The CRM backend is **FastAPI + APScheduler (in-process) + psycopg on Supabase Postgres**. It uses numbered SQL migrations (latest `035_lead_segment_charter.sql`) and already holds `GROQ_API_KEY` and R2 storage credentials. It deploys on **Render `plan: free`** (render.yaml) with a self-ping keepalive.
- Render free tier: spins down after **15 min** without inbound traffic, gives 750 instance hours a month, has no free background workers or cron, and Render says "**Do not use them for production applications**" ([render.com/docs/free](https://render.com/docs/free)) — V.
- Supabase Free: **500 MB** database, paused after **1 week of inactivity**, **no automatic backups**. Pro from $25/mo ([supabase.com/pricing](https://supabase.com/pricing)) — V.

**Implications**
- All jobs go into a **Postgres-backed job table** with leases and idempotency keys, so a spin-down or restart resumes cleanly.
- Inbound mail must not depend on an in-process timer alone.
- Before outbound sending is enabled, the minimum is a paid always-on Render instance **or** an external scheduler. Current Render paid prices were not retrieved in this session.
- Supabase Pro is recommended for backups once email bodies are stored.
- **n8n is not justified.** Every required capability (HTTP, IMAP/API, Postgres, LLM, schedules) already exists in the FastAPI codebase. n8n would add a second runtime, a second secret store and a second audit trail, with no capability gap to close.

## F8. Discovery source priority (resulting design)

1. Regulator lists (DGCA/MoCA, UK SD30/SD31, national EU NCAs). These are machine-parsable PDFs.
2. Human-assisted regulator lookups (FAA dashboard, TC FTU search, CASA). No automated retrieval.
3. Each school's own website. robots-aware crawl. This is the only source for emails.
4. Google Places, only to find a missing website, under the rules in F3.

## F9. Sending capacity (engineering cap, not a target)

The pilot is capped at **≤20 new contacts per day per mailbox**, far below the Hostinger caps (F2). Every send passes the jurisdiction gate (F5), the suppression check, `DRY_RUN=false` and `OUTBOUND_ENABLED=true`. Both flags ship as safe defaults (`DRY_RUN=true`, `OUTBOUND_ENABLED=false`).

## Not verified in this session

- EASA Reg. 2018/1139 articles 62 and 78 (fetch truncated).
- Spam Act Sched. 2 cl. 4 text.
- India Gazette G.S.R. for the DPDP Rules.
- Current Render paid pricing.
- Hostinger webhook event list and signature scheme (undocumented).
- Whether an existing Hostinger plan includes Mail API (needs a check in your hPanel).

## Pending your decisions (blocking Phase 2 scope only)

1. Outreach domain/subdomain and mailbox provider.
2. Partnership proposition and commercial model.
3. Target countries in priority order.
4. AMBER/RED approver.
5. Sign-off of the company facts sheet.
6. Approve paid Render and Supabase tiers before activation.
