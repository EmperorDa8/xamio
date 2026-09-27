"""Short-lived signed tickets for handing identity across a browser redirect.

The Google OAuth consent flow is a chain of top-level navigations, which cannot
carry the Supabase Bearer token. The API mints a ticket naming the user while
it still has the token (/auth/google), the browser carries it through the
redirect, and the callback reads it back to know whose Google account this is.

HMAC-signed and expiring; a ticket is worthless once its few minutes are up.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# Used only when no secret is configured: tickets then survive exactly as long
# as this process, which is enough for a ten-minute consent flow on one worker.
_FALLBACK = secrets.token_bytes(32)


class TicketError(Exception):
    pass


def _key() -> bytes:
    raw = (
        os.getenv("REMINDER_SIGNING_SECRET")
        or os.getenv("TASK_SECRET")
        or os.getenv("SUPABASE_JWT_SECRET")
    )
    return raw.encode() if raw else _FALLBACK


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: str) -> str:
    # Domain-separated from the reminder done-links, which may share the key.
    return _b64(hmac.new(_key(), b"ticket:" + payload.encode(), hashlib.sha256).digest())


def issue(claims: dict, ttl_seconds: int = 600) -> str:
    body = {**claims, "x": int(time.time()) + ttl_seconds}
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{_sign(payload)}"


def verify(ticket: str) -> dict:
    if not ticket or "." not in ticket:
        raise TicketError("missing ticket")
    payload, _, signature = ticket.partition(".")
    if not hmac.compare_digest(signature, _sign(payload)):
        raise TicketError("bad signature")
    try:
        body = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        raise TicketError("malformed ticket")
    if int(body.get("x", 0)) < int(time.time()):
        raise TicketError("expired ticket")
    return body
