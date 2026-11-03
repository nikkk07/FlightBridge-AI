-- FlightBridge 006: other ways to reach a school, found on its own website (LinkedIn page, enquiry form, Facebook …).
-- These are links for a person to use; nothing is scraped from LinkedIn or other platforms.
CREATE TABLE IF NOT EXISTS flightbridge.contact_channel (
    id          BIGSERIAL PRIMARY KEY,
    org_id      UUID NOT NULL REFERENCES flightbridge.organisation(id),
    kind        TEXT NOT NULL CHECK (kind IN ('contact_form','linkedin','facebook','instagram','x','youtube','whatsapp')),
    url         TEXT NOT NULL,
    source_url  TEXT,
    found_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, kind, url)
);
CREATE INDEX IF NOT EXISTS idx_fb_contact_channel_org ON flightbridge.contact_channel (org_id);

ALTER TABLE flightbridge.organisation ADD COLUMN IF NOT EXISTS contacts_searched_at TIMESTAMPTZ;

