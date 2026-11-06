-- 011: Email Auto-Pilot (engine 1.8.0).
-- When the owner switches Auto-Pilot ON in the CRM, the engine sends the owner's APPROVED templates to Eligible
-- schools by itself: a warm-up pace (or the owner's daily number), each school's own business hours, random gaps,
-- automatic follow-ups (owner's count and days), and an automatic pause on bounces, complaints or mailbox errors.
-- Every email still passes the same compliance gates, the kill switch and DRY_RUN / OUTBOUND_ENABLED.
--   system_setting 'send_autopilot'        owner's settings (written by the CRM)
--   system_setting 'send_autopilot_state'  engine's state: pre-flight checklist, pace, pause (written by the engine)

-- up to 5 follow-ups (was 3)
ALTER TABLE flightbridge.follow_up_task DROP CONSTRAINT IF EXISTS follow_up_task_step_check;
ALTER TABLE flightbridge.follow_up_task ADD CONSTRAINT follow_up_task_step_check CHECK (step BETWEEN 1 AND 5);
ALTER TABLE flightbridge.outreach_template DROP CONSTRAINT IF EXISTS outreach_template_kind_check;
ALTER TABLE flightbridge.outreach_template ADD CONSTRAINT outreach_template_kind_check
    CHECK (kind IN ('initial','followup_1','followup_2','followup_3','followup_4','followup_5'));

-- who/what approved an email: a person, or Auto-Pilot on the owner's behalf
ALTER TABLE flightbridge.approval_task ADD COLUMN IF NOT EXISTS decided_via TEXT NOT NULL DEFAULT 'person';
ALTER TABLE flightbridge.approval_task DROP CONSTRAINT IF EXISTS approval_task_decided_via_check;
ALTER TABLE flightbridge.approval_task ADD CONSTRAINT approval_task_decided_via_check CHECK (decided_via IN ('person','autopilot'));

INSERT INTO flightbridge.system_setting (key, value) VALUES
  ('send_autopilot', '{"enabled": false, "daily_limit": 40, "warmup": true}'::jsonb),
  ('send_autopilot_state', '{}'::jsonb)
ON CONFLICT (key) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_fb_outbound_sent_at ON flightbridge.outbound_message (sent_at) WHERE sent_at IS NOT NULL;
