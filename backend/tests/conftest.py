"""Shared test setup.

Every test runs against a throwaway SQLite database with REAL auth enforcement
(an HS256 Supabase secret), so the suite exercises the same code paths as
production rather than the fail-open dev mode. Outbound email and AI calls are
stubbed; nothing leaves the machine.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_DB = Path(tempfile.mkdtemp()) / "test.sqlite"

# Must be set before main/services are imported: several modules read their
# configuration at import time.
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_DB.as_posix()}",
        "SUPABASE_JWT_SECRET": "test-jwt-secret-that-is-long-enough-for-hs256",
        "SUPABASE_URL": "",
        "TASK_SECRET": "test-task-secret",
        "REMINDER_SIGNING_SECRET": "test-signing-secret",
        "PUBLIC_API_URL": "https://api.test",
        "FRONTEND_URL": "https://xamio.app",
        "GOOGLE_CLIENT_ID": "test-client-id",
        "GOOGLE_CLIENT_SECRET": "test-client-secret",
        "GOOGLE_REDIRECT_URI": "https://api.test/auth/google/callback",
        "RESEND_API_KEY": "",
        "SMTP_USER": "",
        "SMTP_PASSWORD": "",
        "GEMINI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
    }
)

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from services import email_service  # noqa: E402

SECRET = os.environ["SUPABASE_JWT_SECRET"]


def make_token(sub="user-1", email="student@uni.edu", aud="authenticated", exp_in=3600, secret=SECRET):
    return jwt.encode(
        {"sub": sub, "email": email, "aud": aud, "exp": int(time.time()) + exp_in},
        secret,
        algorithm="HS256",
    )


def auth_header(**kw):
    return {"Authorization": f"Bearer {make_token(**kw)}"}


@pytest.fixture(autouse=True)
def _reset_state():
    """Fresh rate-limit buckets and an empty queue for every test."""
    main.limiter.reset()
    from services.db import engine

    from services import reminder_queue, token_store

    reminder_queue.init()
    token_store._ready()
    with engine.begin() as conn:
        conn.execute(reminder_queue.reminder_queue.delete())
        conn.execute(token_store._google_tokens.delete())
    yield


@pytest.fixture
def client():
    with TestClient(main.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def sent_mail(monkeypatch):
    """Capture every outbound email instead of sending it."""
    outbox = []

    def fake_send(to, subject, text, ics_bytes=None):
        outbox.append({"to": to, "subject": subject, "text": text, "ics": ics_bytes})

    monkeypatch.setattr(email_service, "_send", fake_send)
    return outbox
