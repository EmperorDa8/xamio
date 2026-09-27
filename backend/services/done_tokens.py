"""Signed "I've done this" links for reminder emails.

A student who has handed something in should be able to stop the rest of its
reminders in one click, from the email, without signing in. MyStudyLife not
having this is a documented reason people abandon it: an item you never mark
complete nags forever, and the only escape is deleting it.

No login means the link itself is the authorisation, so it is signed. The token
carries who it is for and which item it refers to, and an HMAC over both. Worst
case if one leaks is that somebody cancels a reminder — low harm, but a
guessable URL that silenced other people's reminders would be worse, so the
signature is not optional and verification fails closed.

Tokens expire. A link forwarded or sitting in an old mailbox should not stay
live indefinitely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

# Long enough to outlive any ladder (a thesis milestone reminds 30 days out,
# and a student may act on the email weeks later), short enough that an old
# link in a forwarded mailbox stops working.
TOKEN_TTL_SECONDS = 180 * 24 * 3600


class TokenError(Exception):
    """Token missing, malformed, wrongly signed, or expired."""


def _secret() -> bytes:
    """Signing key.

    Falls back to TASK_SECRET so existing deployments get this without setting
    a new variable, but a dedicated key is preferred: TASK_SECRET also guards
    reminder dispatch, and one secret per job limits the blast radius.
    """
    raw = os.getenv("REMINDER_SIGNING_SECRET") or os.getenv("TASK_SECRET")
    if not raw:
        # Fail closed, like the dispatch guard. An unsigned "cancel someone's
        # reminders" endpoint is worse than the feature being unavailable.
        raise TokenError(
            "Reminder done-links are not configured. Set REMINDER_SIGNING_SECRET "
            "(or TASK_SECRET)."
        )
    return raw.encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_token(*, destination: str, item: dict, expires_at: int | None = None) -> str:
    """Sign a link that marks one item done for one recipient.

    `item` carries what the queue needs to find that item's rows again: the
    stable key, plus the course code / date / time that exam dedupe keys were
    built from before stable keys were used for them.
    """
    body = {
        "to": destination,
        "sk": item.get("stable_key"),
        "c": item.get("course_code"),
        "d": item.get("date"),
        "t": item.get("time"),
        "k": item.get("kind") or "exam",
        "x": expires_at or int(time.time()) + TOKEN_TTL_SECONDS,
    }
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def parse_token(token: str) -> dict:
    """Verify and decode. Raises TokenError on anything suspect."""
    if not token or "." not in token:
        raise TokenError("Malformed link.")
    payload, _, signature = token.partition(".")

    expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    # compare_digest rather than == so a wrong signature cannot be narrowed
    # down by timing.
    if not hmac.compare_digest(signature, expected):
        raise TokenError("This link is not valid.")

    try:
        body = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        raise TokenError("Malformed link.")

    if not isinstance(body, dict) or not body.get("to"):
        raise TokenError("Malformed link.")
    if int(body.get("x", 0)) < int(time.time()):
        raise TokenError("This link has expired.")
    return body
