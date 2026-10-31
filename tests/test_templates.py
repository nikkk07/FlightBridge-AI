import psycopg
import pytest

from flightbridge.outreach.templates import (TemplateError, approve_template, render, save_template, validate)


def test_valid_template():
    v = validate("Partnership enquiry for {{school_name}}", "Dear {{school_name}} team in {{city}},\n\nWe teach DGCA theory.")
    assert v["ok"] and v["placeholders"] == ["city", "school_name"]


@pytest.mark.parametrize("subject,body,err", [
    ("Hello", "Dear {{principal}}, we teach DGCA ground subjects here.", "unknown placeholder"),
    ("Re: our chat", "Dear team, we teach DGCA ground subjects here.", "Re:/Fwd:"),
    ("Hello", "We have trained 3000+ pilots and offer 100% placement.", "not approved"),
    ("Hello", "Our flying school is DGCA-approved and very good.", "not approved"),
    ("Hello", "TODO write the real body text for this email.", "unresolved"),
    ("", "Dear team, we teach DGCA ground subjects here.", "subject"),
])
def test_invalid_templates(subject, body, err):
    v = validate(subject, body)
    assert not v["ok"] and any(err in e for e in v["errors"]), v


def test_footer_in_template_is_warned():
    v = validate("Hello", "Dear team, we teach DGCA theory.\n\nReply unsubscribe to stop.")
    assert v["ok"] and v["warnings"]


def test_render_fails_without_value():
    with pytest.raises(TemplateError):
        render("Hi {{school_name}}", "In {{city}} we work.", {"school_name": "X", "city": None})


def test_save_and_approve_versions(clean_db):
    with psycopg.connect(clean_db, autocommit=True) as c:
        emp = str(c.execute("INSERT INTO public.employee (name) VALUES ('A') RETURNING id").fetchone()[0])
    a = save_template(clean_db, "initial", "Enquiry for {{school_name}}", "Dear {{school_name}} team, hello from Delhi.", emp)
    b = save_template(clean_db, "initial", "Second {{school_name}}", "Dear {{school_name}} team, second version here.", emp)
    assert (a["version"], b["version"]) == (1, 2)
    approve_template(clean_db, a["id"], emp)
    approve_template(clean_db, b["id"], emp)          # replaces a; only one approved per kind
    with psycopg.connect(clean_db) as c:
        rows = dict(c.execute("SELECT version, status FROM flightbridge.outreach_template").fetchall())
    assert rows == {1: "archived", 2: "approved"}
    bad = save_template(clean_db, "initial", "Hi", "We are India's best academy with 6000+ students.", emp)
    assert not bad["validation"]["ok"]
    with pytest.raises(TemplateError):
        approve_template(clean_db, bad["id"], emp)
