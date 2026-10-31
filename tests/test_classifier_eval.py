"""Evaluation of the rule-based classifier on a SYNTHETIC labelled set (tests/data/inbound_eval.jsonl).
Release gates (must all hold):
  - zero incorrect GREEN on anything labelled AMBER/RED (the dangerous error: an unsafe auto-path)
  - 100% recall on unsubscribe, bounce, auto-reply and RED (legal/privacy/complaint/fraud/injection)
  - risk accuracy >= 95%, intent accuracy >= 90%
The set is synthetic; real replies from the pilot must be added before any auto-reply is enabled."""
import json
import pathlib

from flightbridge.ai.classify import classify

ROWS = [json.loads(l) for l in (pathlib.Path(__file__).parent / "data" / "inbound_eval.jsonl").read_text().splitlines() if l.strip()]


def run():
    return [(r, classify(r["subject"], r["text"], r.get("from"))) for r in ROWS]


def test_eval_size():
    assert len(ROWS) >= 40


def test_no_false_green():
    bad = [(r["text"], c.risk) for r, c in run() if r["risk"] != "GREEN" and c.risk == "GREEN"]
    assert bad == []


def test_full_recall_on_safety_classes():
    res = run()
    for flag, attr in (("unsubscribe", "is_unsubscribe"), ("bounce", "is_bounce"), ("auto", "is_auto_reply")):
        missed = [r["text"] for r, c in res if r.get(flag) and not getattr(c, attr)]
        assert missed == [], (flag, missed)
    red_missed = [r["text"] for r, c in res if r["risk"] == "RED" and c.risk != "RED"]
    assert red_missed == []


def test_accuracy_gates():
    res = run()
    risk_acc = sum(c.risk == r["risk"] for r, c in res) / len(res)
    intent_acc = sum(c.intent == r["intent"] for r, c in res) / len(res)
    assert risk_acc >= 0.95, risk_acc
    assert intent_acc >= 0.90, intent_acc


def test_quoted_history_is_ignored():
    c = classify("Re: x", "Yes, we are interested.\n\nOn Tue, We One wrote:\n> pricing and commission details")
    assert c.risk == "GREEN"
