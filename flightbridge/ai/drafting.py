"""Reply drafts built ONLY from config/approved_facts.json. Every draft goes to a human approval task
(auto-reply is disabled in this release). AMBER drafts acknowledge without committing; RED drafts carry a
[[HUMAN RESPONSE REQUIRED]] marker that the send-time content gate rejects, so an unedited RED draft can never go out.
"""
from __future__ import annotations

import json
from pathlib import Path

FACTS_PATH = Path(__file__).resolve().parents[1] / "config" / "approved_facts.json"
NO_DRAFT_INTENTS = {"unsubscribe_request", "partnership_rejection", "auto_reply", "email_delivery_issue"}
HUMAN_MARKER = "[[HUMAN RESPONSE REQUIRED]]"


def load_facts(path: Path = FACTS_PATH) -> dict:
    return {f["id"]: f["text"] for f in json.loads(path.read_text(encoding="utf-8"))["facts"]}


def reply_subject(original: str) -> str:
    s = (original or "").strip() or "Partnership enquiry"
    return s if s.lower().startswith("re:") else f"Re: {s}"[:150]


def build_reply(intent: str, risk: str, questions: list[str], org_name: str, facts: dict) -> dict | None:
    """Returns {subject_hint, body, evidence, escalation_reason} or None when no reply should be drafted."""
    if intent in NO_DRAFT_INTENTS:
        return None
    greet = f"Dear {org_name} team,"
    if risk == "RED":
        return {"body": f"{greet}\n\n{HUMAN_MARKER}\n", "evidence": [],
                "escalation_reason": f"RED ({intent}): a person must read this and reply personally"}
    if risk == "AMBER":
        listed = "\n".join(f"- {q}" for q in questions) if questions else "- (no explicit question detected)"
        return {"body": (f"{greet}\n\nThank you for your reply. We have noted your questions and a member of our team "
                         f"will respond to them personally.\n\n{facts['contact']}\n\nKind regards,\nWe One Aviation"),
                "evidence": ["contact"],
                "escalation_reason": f"AMBER ({intent}): needs a human decision before answering.\nQuestions detected:\n{listed}"}
    # GREEN
    used = ["location", "founded", "ground_school", "counselling"]
    if intent == "positive_interest":
        used.append("purpose")
    used.append("contact")
    body = f"{greet}\n\nThank you for your reply. " + " ".join(facts[k] for k in used) + "\n\nKind regards,\nWe One Aviation"
    return {"body": body, "evidence": used,
            "escalation_reason": f"GREEN ({intent}): low risk, but auto-reply is disabled so a human approves every reply"}
