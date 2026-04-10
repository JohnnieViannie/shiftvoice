"""
Machine-readable intent labels (snake_case) for car-hailing voice flows.

- customer_* : text going to TTS (rider / platform message read to driver)
- driver_*   : text from STT (driver speech → customer)

Use INTENT_CLASSIFIER=heuristic | openai | hybrid (default: hybrid when OPENAI_API_KEY is set, else heuristic).
"""

from __future__ import annotations

import os
import re

# --- Allowed labels (single token, snake_case) ---------------------------------
CUSTOMER_INTENTS = frozenset(
    {
        "customer_requests_pickup",
        "customer_rejects_pickup",
        "customer_cancels_request",
        "customer_general_message",
        "customer_eta_or_location_detail",
        "unclear",
    }
)

DRIVER_INTENTS = frozenset(
    {
        "driver_accepts_request",
        "driver_rejects_request",
        "driver_eta_update",
        "driver_general_acknowledgment",
        "unclear",
    }
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def heuristic_customer_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "unclear"
    if any(
        x in t
        for x in (
            "cancel",
            "cancelled",
            "never mind",
            "nevermind",
            "don't need",
            "dont need",
            "no longer need",
        )
    ):
        return "customer_cancels_request"
    if any(
        x in t
        for x in (
            "reject",
            "don't want",
            "dont want",
            "no thanks",
            "not interested",
            "changed my mind",
        )
    ):
        return "customer_rejects_pickup"
    if any(
        x in t
        for x in (
            "pick me",
            "pickup",
            "pick up",
            "come to",
            "waiting at",
            "i'm at",
            "im at",
            "can you get",
            "need a ride",
            "need a lift",
        )
    ):
        return "customer_requests_pickup"
    if any(x in t for x in ("where are you", "how long", "eta", "minutes away", "arriving")):
        return "customer_eta_or_location_detail"
    return "customer_general_message"


def heuristic_driver_intent(text: str) -> str:
    t = _norm(text)
    if not t:
        return "unclear"
    if any(
        x in t
        for x in (
            "can't take",
            "cannot take",
            "cant take",
            "reject",
            "decline",
            "sorry i can't",
            "not available",
            "too far",
            "busy",
        )
    ):
        return "driver_rejects_request"
    if any(
        x in t
        for x in (
            "accept",
            "accepted",
            "on my way",
            "coming",
            "heading",
            "yes i can",
            "i'll be there",
            "ill be there",
            "be there in",
            "got it",
            "see you",
        )
    ):
        return "driver_accepts_request"
    if any(x in t for x in ("minute", "minutes", "eta", "arriving in", "there in", "away")):
        return "driver_eta_update"
    if any(x in t for x in ("ok", "okay", "sure", "alright", "thanks")):
        return "driver_general_acknowledgment"
    return "unclear"


def _openai_classify(text: str, role: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not (text or "").strip():
        return None
    allowed = CUSTOMER_INTENTS if role == "customer" else DRIVER_INTENTS
    allowed_list = ", ".join(sorted(allowed))
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = os.getenv("INTENT_CLASSIFIER_MODEL", "gpt-4o-mini")
        who = "rider/customer message (read aloud to the driver)" if role == "customer" else "driver spoken reply (send to rider)"
        completion = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=40,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify short car-hailing / ride-hail utterances. "
                        f"Reply with exactly ONE label from this list and nothing else: {allowed_list}."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Classify this {who}:\n\n{text}",
                },
            ],
        )
        raw = (completion.choices[0].message.content or "").strip().lower()
        raw = raw.replace(" ", "_").replace("-", "_")
        re.sub(r"(^[`'\"]+)|([`'\"]+$)", "", raw)
        if raw in allowed:
            return raw
        for token in raw.replace(",", " ").split():
            re.sub(r"(^[`'\"]+)|([`'\"]+$)", "", token.strip())
            token = token.replace("-", "_")
            if token in allowed:
                return token
        return None
    except Exception:
        return None


def resolve_intent(text: str, role: str) -> str:
    """
    role: 'customer' (TTS input) or 'driver' (STT output).

    INTENT_CLASSIFIER: heuristic | openai | hybrid (default: hybrid if OPENAI_API_KEY else heuristic).
    Hybrid: strong keyword hits use heuristics; otherwise OpenAI when available.
    """
    raw = (os.getenv("INTENT_CLASSIFIER") or "").strip().lower()
    if not raw:
        raw = "hybrid" if os.getenv("OPENAI_API_KEY") else "heuristic"
    if raw not in ("heuristic", "openai", "hybrid"):
        raw = "hybrid"

    if role == "customer":
        h = heuristic_customer_intent(text)
        if raw == "heuristic":
            return h
        ai = _openai_classify(text, "customer")
        if raw == "openai":
            return ai or h
        if h in ("customer_requests_pickup", "customer_rejects_pickup", "customer_cancels_request"):
            return h
        return ai or h

    h = heuristic_driver_intent(text)
    if raw == "heuristic":
        return h
    ai = _openai_classify(text, "driver")
    if raw == "openai":
        return ai or h
    if h in ("driver_accepts_request", "driver_rejects_request"):
        return h
    return ai or h
