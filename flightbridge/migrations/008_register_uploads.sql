-- 008: official lists uploaded by a person in the CRM (first: the FAA Part 141 pilot-school list).
-- The CRM stores the file here; the engine imports it on its next cycle, then clears the file content and keeps the
-- result (rows read, schools matched / added) for the audit trail.
CREATE TABLE IF NOT EXISTS flightbridge.register_upload (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('faa_part141')),
    filename     TEXT NOT NULL,
    content_b64  TEXT,
    sha256       TEXT NOT NULL,
    uploaded_by  UUID REFERENCES public.employee(id),
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'imported', 'error')),
    result       JSONB,
    processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_fb_register_upload_pending ON flightbridge.register_upload (uploaded_at) WHERE status = 'pending';
