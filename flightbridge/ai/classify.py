"""Deterministic first-pass understanding of an inbound email.

Order: strip quoted history -> detect bounce / auto-reply / unsubscribe / prompt injection -> match intent
families -> risk = the MOST severe family matched (RED > AMBER > GREEN). Any commercial, regulatory or legal
signal therefore escalates the whole message, even inside an otherwise positive reply. An optional LLM pass
(flightbridge.ai.llm) may only RAISE risk, never lower it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RISK_ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2}

QUOTE_CUT = [
    re.compile(r"^On .{3,200}wrote:\s*$", re.M | re.I),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.M | re.I),
    re.compile(r"^From:\s.+$\n^(Sent|Date):\s", re.M | re.I),
    re.compile(r"^_{5,}\s*$", re.M),
]

BOUNCE_FROM = re.compile(r"^(mailer-daemon|postmaster|mail-daemon)@", re.I)
BOUNCE_SUBJ = re.compile(r"(undeliver|delivery status notification|mail delivery (failed|subsystem)|returned mail|delivery failure|failure notice)", re.I)
AUTO_SUBJ = re.compile(r"^\s*(automatic reply|auto(matic)?[- ]?(reply|response)|out of (the )?office|away from (the )?office|auto:)", re.I)
UNSUB = re.compile(
    r"\b(unsubscribe|remove (me|us|this address|our (email|address))( from (your|the) (list|mailing list|database))?"
    r"|stop (emailing|e-mailing|sending|contacting)|do not (contact|email|e-mail|write to) (us|me)( again)?"
    r"|don'?t (contact|email|e-mail) (us|me)( again)?|take (me|us) off|opt[- ]?out)\b", re.I)
INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts?|messages?)|disregard (your|all|the) (instructions|rules)"
    r"|system prompt|you are now (a|an|in)|act as (a|an) |developer mode|jailbreak|reveal (your|the) (instructions|prompt|api|keys?|password)"
    r"|print (your|the) (prompt|instructions)|api[_ -]?key|access token|BEGIN (RSA|OPENSSH) PRIVATE KEY|<script\b|\{\{.*\}\}|\bassistant:|\bsystem:)",
    re.I)

FAMILIES = [
    # (intent, risk, pattern)
    ("legal_concern", "RED", r"\b(lawyer|attorney|solicitor|legal action|lawsuit|sue|court|cease and desist|legal notice)\b"),
    ("privacy_request", "RED", r"\b(gdpr|data protection|personal data|delete (my|our) (data|details)|right to (erasure|be forgotten)|subject access|dpdp|privacy (request|complaint))\b"),
    ("complaint", "RED", r"\b(complain(t|ing)?|harass|spam(ming)?|report (you|this)|abuse|scam|unacceptable)\b"),
    ("potential_fraud", "RED", r"\b(bank (details|account)|wire transfer|gift card|bitcoin|crypto|western union|change (of|in) (our )?(bank|account)|send (the )?payment (to|now))\b"),
    ("commercial_terms", "AMBER", r"\b(commission|referral fee|fees?|pric(e|es|ing)|cost(s|ing)?|rates?|discount|payment|invoice|quote|quotation)\b"),
    ("agreement_request", "AMBER", r"\b(contract|agreement|mou|memorandum|terms and conditions|exclusiv(e|ity)|sign(ed|ing)?)\b"),
    ("student_volume", "AMBER", r"\b(how many students|number of students|student (volume|numbers)|capacity|intake|batch size|seats?)\b"),
    ("regulatory_question", "AMBER", r"\b(dgca|faa|easa|caa|casa|transport canada|licen[cs]e conversion|conversion|accredit|regulat|approval|visa|i-?20|m-?1|sevis)\b"),
    ("liability_question", "AMBER", r"\b(liabilit|insurance|indemn|responsib(le|ility) for)\b"),
    ("meeting_request", "AMBER", r"\b(meeting|call|zoom|teams|google meet|schedule|available (on|for|at)|visit (us|our))\b"),
    ("partnership_rejection", "GREEN", r"\b(not interested|no,? thank(s| you)|not at this time|we (do not|don'?t) (work|partner) with (agents|agencies|third parties)|decline|no longer accepting|not looking for)\b"),
    ("profile_request", "GREEN", r"\b(brochure|company profile|more (information|info|details)|tell (me|us) more|send (us|me) (some )?(details|information)|who are you|website)\b"),
    ("positive_interest", "GREEN", r"\b(interested|happy to (discuss|explore|help)|sounds (good|great|interesting)|keen|would (like|love) to|open to)\b"),
    ("acknowledgement", "GREEN", r"\b(thank(s| you)|received|noted|got it)\b"),
]
FAMILIES = [(i, r, re.compile(p, re.I)) for i, r, p in FAMILIES]

INTENT_PRIORITY = [f[0] for f in FAMILIES]   # earlier = more specific / more severe


@dataclass
class Classification:
    intent: str
    risk: str
    confidence: float
    signals: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    injection: bool = False
    is_bounce: bool = False
    is_auto_reply: bool = False
    is_unsubscribe: bool = False
    new_text: str = ""
    classifier: str = "rules-v1"


def strip_quoted(text: str) -> str:
    cut = len(text)
    for rx in QUOTE_CUT:
        m = rx.search(text)
        if m:
            cut = min(cut, m.start())
    lines = [ln for ln in text[:cut].splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def extract_questions(text: str) -> list[str]:
    qs = [q.strip() for q in re.findall(r"[^.?!\n]{6,300}\?", text)]
    return list(dict.fromkeys(qs))[:10]


def classify(subject: str, text: str, from_address: str | None = None, headers: dict | None = None) -> Classification:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    new = strip_quoted(text or "")
    probe = f"{subject or ''}\n{new}"
    c = Classification("unclear", "AMBER", 0.5, new_text=new)

    ctype = headers.get("content-type", "")
    if (from_address and BOUNCE_FROM.match(from_address)) or BOUNCE_SUBJ.search(subject or "") \
            or "report-type=delivery-status" in ctype.replace(" ", "").lower() or "x-failed-recipients" in headers:
        c.is_bounce, c.intent, c.risk, c.confidence = True, "email_delivery_issue", "GREEN", 0.95
        c.signals.append("bounce")
        return c
    auto = headers.get("auto-submitted", "no").lower()
    if (auto and auto != "no") or "x-autoreply" in headers or "x-autorespond" in headers \
            or headers.get("precedence", "").lower() in ("auto_reply", "bulk", "junk") or AUTO_SUBJ.search(subject or ""):
        c.is_auto_reply, c.intent, c.risk, c.confidence = True, "auto_reply", "GREEN", 0.9
        c.signals.append("auto_reply")
        return c

    c.questions = extract_questions(new)
    if INJECTION.search(probe):
        c.injection = True
        c.signals.append("prompt_injection_pattern")
    if UNSUB.search(probe):
        c.is_unsubscribe = True
        c.signals.append("unsubscribe")

    matched = [(i, r) for i, r, rx in FAMILIES if rx.search(probe)]
    c.signals.extend(i for i, _ in matched)
    if c.is_unsubscribe:
        c.intent, c.risk, c.confidence = "unsubscribe_request", "GREEN", 0.95
        if any(r == "RED" for _, r in matched) or c.injection:
            c.risk = "RED"                  # e.g. "stop emailing us or we will report you" -> human sees it
        return c
    if c.injection:
        c.intent, c.risk, c.confidence = "potentially_malicious", "RED", 0.9
        return c
    if not matched:
        return c                            # unclear -> AMBER, human review
    worst = max(matched, key=lambda m: RISK_ORDER[m[1]])
    c.risk = worst[1]
    c.intent = min((m for m in matched if m[1] == c.risk), key=lambda m: INTENT_PRIORITY.index(m[0]))[0]
    # GREEN only when every matched family is GREEN and there are no open questions outside GREEN topics.
    if c.risk == "GREEN" and len(c.questions) > 2:
        c.risk, c.signals = "AMBER", c.signals + ["many_questions"]
    distinct = len({r for _, r in matched})
    c.confidence = 0.9 if distinct == 1 and len(matched) <= 2 else 0.7
    return c
