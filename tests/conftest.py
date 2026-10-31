import os
import uuid
from datetime import datetime, timezone

import pytest

from flightbridge.compliance.content import SenderProfile
from flightbridge.compliance.rules import load_rules

ADMIN_URL = os.environ.get("FB_TEST_ADMIN_URL", "postgresql://postgres@/postgres?host=/tmp&port=55432")

FULL_PROFILE = SenderProfile(
    legal_name="We One Aviation",
    postal_address="C-404, 3rd Floor, Sector-7, Dwarka, New Delhi 110077, India",
    contact_email="partners@example.org",
    phone="+91 96673 70747",
    opt_out_text='Reply "unsubscribe" and we will not contact you again.',
    ad_disclosure_text="This is a business partnership proposal from We One Aviation.",
    privacy_notice_text="We hold your business email under legitimate interests; see example.org/privacy.",
)


@pytest.fixture(scope="session")
def rules():
    return load_rules()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _pg_available():
    try:
        import psycopg
        with psycopg.connect(ADMIN_URL, connect_timeout=3):
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_url():
    if not _pg_available():
        pytest.skip("Postgres not available (set FB_TEST_ADMIN_URL)")
    import psycopg
    from flightbridge import db as fbdb
    name = f"fb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    url = ADMIN_URL.replace("/postgres?", f"/{name}?")
    with psycopg.connect(url, autocommit=True) as c:
        # minimal stand-in for the CRM's public.employee (FK target)
        c.execute("CREATE TABLE public.employee (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT NOT NULL, "
                  "department TEXT, active BOOLEAN DEFAULT true)")
    fbdb.migrate(url)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def clean_db(db_url):
    import psycopg
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("""TRUNCATE flightbridge.outbound_message, flightbridge.compliance_check, flightbridge.approval_task,
                     flightbridge.ai_draft, flightbridge.email_message, flightbridge.email_thread,
                     flightbridge.follow_up_task, flightbridge.suppression, flightbridge.contact_fact_evidence,
                     flightbridge.contact, flightbridge.qualification_result, flightbridge.research_fact,
                     flightbridge.website_page, flightbridge.regulatory_approval, flightbridge.organisation_identifier,
                     flightbridge.organisation, flightbridge.audit_log, flightbridge.job, flightbridge.outreach_template,
                     flightbridge.ai_classification, flightbridge.webhook_event, flightbridge.mailbox_cursor,
                     public.employee CASCADE""")
        c.execute("INSERT INTO flightbridge.system_setting (key, value) VALUES ('outbound_kill_switch','true'::jsonb),"
                  "('auto_reply_kill_switch','true'::jsonb) ON CONFLICT (key) DO UPDATE SET value='true'::jsonb")
        c.execute("INSERT INTO flightbridge.system_setting (key, value) VALUES ('followup_schedule_days','[4, 9, 16]'::jsonb),"
                  "('followup_business_hours','{\"start\": 9, \"end\": 17}'::jsonb) ON CONFLICT (key) DO NOTHING")
    return db_url
