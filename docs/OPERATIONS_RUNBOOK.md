# FlightBridge Operations Runbook

Updated 2026-10-31. Safe defaults: `DRY_RUN=true`, `OUTBOUND_ENABLED=false`, kill switch engaged, auto-reply off.

## The cycle (what runs, in order)

`python -m flightbridge.cli run-once` (or `worker --interval 300` to repeat):

1. **Inbox**: read new mail since the saved position (`INBOUND_SOURCE=hostinger` or `imap`). Each message is stored once, linked to its conversation (reply headers → our `Ref:` token → sender address → sender's company domain; never by shared providers such as gmail.com), then classified.
2. **Automatic effects** (deterministic, no AI judgement):
   - bounce → contact marked bounced and suppressed
   - "unsubscribe" or similar → sender and school contact suppressed
   - "not interested" → suppressed, school marked rejected
   - any genuine reply → follow-ups cancelled
   - out-of-office → nothing changes
3. **Drafts**: every reply gets a draft and an approval task in the CRM:
   - GREEN = facts-only reply
   - AMBER = holding reply, with the questions listed for you
   - RED = a person must write the answer; an unedited RED draft is blocked at send time
4. **Follow-ups**: scheduled only after a real send (days set in CRM → Metrics & settings; default 4, 9, 16; weekdays, 9:00–17:00 recipient time). A due follow-up becomes a draft only if you have approved a follow-up template for that step.
5. **Sending**: approved tasks go through the two compliance gates; with `DRY_RUN=true` they are only rendered.

## Your controls in the CRM (Partnerships tab)

| View | Use |
|---|---|
| Email templates | Paste the first email and up to 3 follow-ups. Allowed placeholders: `{{school_name}}`, `{{city}}`, `{{country}}`. Save checks the text; Approve makes it live for new drafts |
| Approval queue | Approve, edit-and-approve, reject, request research. Edits are logged with the original |
| Inbox | Every reply with its risk, intent and how it was matched. Unknown senders appear as "unmatched" |
| Outbound & checks | Why each email passed, was blocked, or needs review |
| Metrics & settings | Contacted, replied, response rate (opens are not tracked), follow-up schedule |
| Suppression | Permanent opt-outs; domain scope refused for shared providers |
| Kill switch | Stops all outbound immediately; inbound keeps working |

## Before the first real email (all required)

1. Approve a first-email template in the CRM.
2. Route mail for the sending domain to Hostinger and authorise Hostinger in SPF (DNS change; needs your approval). Today `weoneaviation.com` MX = `weoneaviation.com` (129.121.97.39) and SPF authorises only that IP, so Hostinger cannot see replies and its sends would fail SPF.
3. Apply CRM migrations 036 and 037 in Supabase and merge the CRM branch.
4. Choose where the worker runs (Mac Mini on demand, or a paid always-on server).
5. Pilot list of 10–20 schools you choose. Then set `DRY_RUN=false`, `OUTBOUND_ENABLED=true` and release the kill switch. All three are needed.

## If something goes wrong

| Symptom | Action |
|---|---|
| Anything unexpected is going out | CRM → kill switch ON (instant). Then set `OUTBOUND_ENABLED=false` |
| Message stuck in `send_unknown` | Do not resend. Check the Sent folder (`INBOX.Sent`) for its `Ref: FB-…` token, then mark it manually |
| Inbox errors | The cycle continues; check `inbound_error` in the run output (token, IMAP password, Cloudflare) |
| Wrong suppression | Suppression is permanent by design; contact the developer to add a documented correction |
