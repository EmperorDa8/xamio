"""End-to-end API tests: every endpoint, the way production runs it (auth on)."""
import threading
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from icalendar import Calendar

import main
from conftest import auth_header, make_token
import samples
from services import done_tokens, email_service, reminder_queue
from services.db import engine

COURSES = "CSC201, MTH211, CSC209"


def future(days, t="09:00", **extra):
    return {
        "course_code": extra.pop("course_code", "CSC201"),
        "course_name": extra.pop("course_name", "Data Structures"),
        "date": (date.today() + timedelta(days=days)).isoformat(),
        "time": t,
        "duration_minutes": 120,
        "venue": "Hall B",
        **extra,
    }


def upload(client, name, data, courses=COURSES, headers=None):
    return client.post(
        "/parse",
        files={"file": (name, data)},
        data={"courses_text": courses},
        headers=headers if headers is not None else auth_header(),
    )


# ─────────────────────────────── health & auth ───────────────────────────────

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Token abc"},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": f"Bearer {make_token(exp_in=-10)}"},
        {"Authorization": f"Bearer {make_token(aud='anon')}"},
        {"Authorization": f"Bearer {make_token(secret='wrong-secret-wrong-secret-wrong-secret')}"},
    ],
    ids=["missing", "wrong-scheme", "garbage", "expired", "wrong-aud", "wrong-signature"],
)
def test_protected_endpoints_reject_bad_tokens(client, headers):
    for method, path, kw in [
        ("post", "/parse", {"files": {"file": ("a.txt", b"x")}}),
        ("post", "/download/ics", {"json": {"exams": []}}),
        ("post", "/alerts/email", {"json": {"email": "a@b.c", "exams": []}}),
        ("post", "/sync/google", {"json": {"exams": []}}),
        ("get", "/auth/google", {}),
        ("get", "/auth/google/status", {}),
    ]:
        r = getattr(client, method)(path, headers=headers, **kw)
        assert r.status_code == 401, (path, r.status_code, r.text)


# ─────────────────────────────────── parsing ──────────────────────────────────

@pytest.mark.parametrize("fmt", sorted(samples.FORMATS))
def test_parse_every_format(client, fmt):
    r = upload(client, f"timetable.{fmt}", samples.FORMATS[fmt]())
    assert r.status_code == 200, r.text
    body = r.json()
    got = {e["course_code"]: (e["date"], e["time"]) for e in body["exams"]}
    assert set(got) == {"CSC201", "MTH211", "CSC209"}, got
    for code in got:
        assert got[code] == samples.expected(code), (code, got[code])
    assert body["unmatched_courses"] == []


def test_parse_reports_unmatched_courses(client):
    r = upload(client, "t.txt", samples.as_txt(), courses="CSC201\nBIO999")
    assert r.status_code == 200, r.text
    assert "BIO999" in r.json()["unmatched_courses"]


def test_course_matching_is_exact_not_substring(client):
    """Registering ENG101 must not hand the student SENG101's exam too."""
    r = upload(client, "t.txt", samples.as_txt(), courses="ENG101")
    assert r.status_code == 200, r.text
    codes = [e["course_code"] for e in r.json()["exams"]]
    assert codes == ["ENG101"], codes


def test_course_line_with_name_still_matches(client):
    r = upload(client, "t.txt", samples.as_txt(), courses="CSC 201 - Data Structures")
    assert r.status_code == 200, r.text
    assert [e["course_code"] for e in r.json()["exams"]] == ["CSC201"]


@pytest.mark.parametrize("name", ["evil.exe", "noextension", "archive.zip", "page.html"])
def test_parse_rejects_unsupported_types(client, name):
    assert upload(client, name, b"hello").status_code == 400


def test_parse_rejects_oversized_upload(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)
    r = upload(client, "t.txt", b"a" * 2048)
    assert r.status_code == 413


@pytest.mark.parametrize(
    "name,data",
    [
        ("empty.txt", b""),
        ("broken.pdf", b"%PDF-1.4 this is not really a pdf"),
        ("broken.xlsx", b"PK\x03\x04 not a zip"),
        ("broken.docx", b"garbage"),
        ("empty.csv", b""),
    ],
)
def test_corrupt_files_fail_cleanly(client, name, data):
    r = upload(client, name, data)
    assert r.status_code in (400, 422), (r.status_code, r.text)
    assert "Traceback" not in r.text


def test_legacy_xls_is_supported(client):
    """.xls is on the allow-list and in the file picker, so it must not die on a
    missing optional dependency."""
    ole_header = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 512
    r = upload(client, "old.xls", ole_header)
    assert "optional dependency" not in r.text.lower(), r.text
    assert "xlrd" not in r.text.lower(), r.text


def test_image_upload_without_ocr_gives_a_usable_answer(client):
    """Render has no tesseract binary. A photo of the timetable must not surface
    a raw 'tesseract is not installed' error to the student."""
    r = upload(client, "photo.png", samples.as_png())
    assert "tesseract" not in r.text.lower(), r.text
    assert "PATH" not in r.text, r.text


def test_image_upload_uses_vision_model_when_ocr_missing(client, monkeypatch):
    from services import parser

    seen = {}

    def fake_vision(image_bytes, mime, prompt):
        seen["mime"] = mime
        d, t = samples.expected("CSC201")
        return '{"items":[{"course_code":"CSC201","date":"%s","time":"%s","kind":"exam"}]}' % (d, t)

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr(parser, "_call_gemini_vision", fake_vision, raising=False)
    r = upload(client, "photo.png", samples.as_png(), courses="CSC201")
    assert r.status_code == 200, r.text
    assert [e["course_code"] for e in r.json()["exams"]] == ["CSC201"]
    assert seen["mime"] == "image/png"


def test_parse_is_rate_limited_per_client(client):
    codes = [upload(client, "t.txt", samples.as_txt()).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5, codes
    assert codes[5] == 429


def test_large_timetable_parses(client):
    r = upload(client, "big.txt", samples.big_txt(400), courses="ZZZ1200, YYY1399")
    assert r.status_code == 200, r.text
    assert len(r.json()["exams"]) == 2


# ────────────────────────────────── .ics export ────────────────────────────────

def _ics(client, exams, **extra):
    return client.post("/download/ics", json={"exams": exams, **extra}, headers=auth_header())


def test_ics_export_is_a_valid_calendar(client):
    exams = [
        future(5, course_code="CSC201"),
        future(6, "13:00", course_code="MTH211", venue="LT 3"),
        future(7, "23:59", course_code="ENG203", kind="coursework", title="Essay 2", venue=None),
    ]
    r = _ics(client, exams, timezone="Africa/Lagos")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/calendar")
    cal = Calendar.from_ical(r.content)
    events = [c for c in cal.walk("VEVENT")]
    assert len(events) == 3
    by_summary = {str(e["summary"]): e for e in events}
    exam = by_summary["EXAM: CSC201 — Data Structures"]
    assert exam["dtstart"].dt.hour == 9
    assert str(exam["dtstart"].dt.tzinfo) in ("Africa/Lagos", "WAT")
    assert (exam["dtend"].dt - exam["dtstart"].dt) == timedelta(minutes=120)
    assert len(exam.walk("VALARM")) >= 2
    essay = by_summary["DUE: ENG203 — Essay 2"]
    assert (essay["dtend"].dt - essay["dtstart"].dt) == timedelta(minutes=15)
    # Re-exporting keeps the same UIDs, so calendar apps update instead of duplicating.
    again = Calendar.from_ical(_ics(client, exams, timezone="Africa/Lagos").content)
    assert {str(e["uid"]) for e in again.walk("VEVENT")} == {str(e["uid"]) for e in events}


def test_ics_custom_mode_uses_chosen_reminders(client):
    r = _ics(client, [future(5)], reminder_mode="custom", reminder_minutes=[60, 30])
    alarms = Calendar.from_ical(r.content).walk("VALARM")
    assert sorted(a["trigger"].dt for a in alarms) == [timedelta(minutes=-60), timedelta(minutes=-30)]


@pytest.mark.parametrize("bad", [{"date": "not-a-date"}, {"date": ""}, {"time": "25:99"}])
def test_ics_rejects_unusable_rows(client, bad):
    r = _ics(client, [{**future(5), **bad}])
    assert r.status_code == 422


def test_ics_unknown_timezone_is_a_client_error(client):
    r = _ics(client, [future(5)], timezone="Mars/Olympus_Mons")
    assert r.status_code in (200, 422), r.status_code
    assert r.status_code != 500


def test_ics_rejects_bad_payload_shape(client):
    assert client.post("/download/ics", json={"nope": 1}, headers=auth_header()).status_code == 422
    assert client.post("/download/ics", json={"exams": [{"date": "x"}]}, headers=auth_header()).status_code == 422


def test_ics_handles_hundreds_of_rows(client):
    exams = [future(5 + i % 60, course_code=f"C{i:04d}") for i in range(500)]
    r = _ics(client, exams)
    assert r.status_code == 200
    assert len(Calendar.from_ical(r.content).walk("VEVENT")) == 500


# ─────────────────────────────── email + reminders ────────────────────────────

def _email(client, exams, to="student@uni.edu", **extra):
    return client.post(
        "/alerts/email",
        json={"email": to, "exams": exams, "timezone": "Africa/Lagos", **extra},
        headers=auth_header(),
    )


def _queue():
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(reminder_queue.reminder_queue.select())]


def test_email_sends_summary_and_queues_reminders(client, sent_mail):
    r = _email(client, [future(20), future(21, course_code="MTH211")])
    assert r.status_code == 200, r.text
    assert r.json()["scheduled"] > 0
    assert len(sent_mail) == 1
    assert sent_mail[0]["to"] == "student@uni.edu"
    assert sent_mail[0]["ics"] and b"BEGIN:VCALENDAR" in sent_mail[0]["ics"]
    assert all(row["status"] == "pending" for row in _queue())


def test_email_only_to_own_address(client, sent_mail):
    r = _email(client, [future(20)], to="someone-else@victim.com")
    assert r.status_code == 403
    assert sent_mail == [] and _queue() == []


def test_email_address_match_is_case_insensitive(client, sent_mail):
    assert _email(client, [future(20)], to="  Student@UNI.edu ").status_code == 200


def test_rescheduling_is_idempotent(client, sent_mail):
    exams = [future(20), future(21, course_code="MTH211")]
    _email(client, exams)
    first = sorted(r["dedupe_key"] for r in _queue())
    _email(client, exams)
    second = sorted(r["dedupe_key"] for r in _queue())
    assert first == second


def test_past_exam_queues_nothing(client, sent_mail):
    r = _email(client, [future(-2)])
    assert r.status_code == 200
    assert r.json()["scheduled"] == 0


def test_email_without_transport_is_a_clear_error(client):
    r = _email(client, [future(20)])
    assert r.status_code == 501
    assert "not configured" in r.json()["detail"].lower()


def test_email_rejects_empty_schedule(client, sent_mail):
    assert _email(client, []).status_code == 400


# ─────────────────────────────────── dispatch ─────────────────────────────────

TASK = {"X-Task-Key": "test-task-secret"}


def _enqueue(send_at, payload=None, key=None, to="student@uni.edu"):
    reminder_queue.enqueue(
        destination=to,
        payload=payload or {**future(3), "stage": "final", "lead_minutes": 120, "kind": "exam"},
        send_at=send_at,
        dedupe_key=key or f"k-{send_at.timestamp()}-{id(payload)}",
    )


def test_dispatch_requires_task_secret(client, monkeypatch):
    assert client.post("/tasks/dispatch-reminders").status_code == 401
    assert client.post("/tasks/dispatch-reminders", headers={"X-Task-Key": "nope"}).status_code == 401
    monkeypatch.delenv("TASK_SECRET")
    assert client.post("/tasks/dispatch-reminders", headers=TASK).status_code == 503


def test_dispatch_sends_due_and_leaves_future(client, sent_mail):
    now = datetime.now(timezone.utc)
    _enqueue(now - timedelta(minutes=1), key="due")
    _enqueue(now + timedelta(hours=5), key="later")
    r = client.post("/tasks/dispatch-reminders", headers=TASK).json()
    assert r["sent"] == 1 and r["failed"] == 0
    assert len(sent_mail) == 1
    statuses = {row["dedupe_key"]: row["status"] for row in _queue()}
    assert statuses == {"due": "sent", "later": "pending"}
    # A second run must not send it again.
    assert client.post("/tasks/dispatch-reminders", headers=TASK).json()["sent"] == 0


def test_failed_send_backs_off_then_gives_up(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_service, "_send", boom)
    _enqueue(datetime.now(timezone.utc) - timedelta(minutes=1), key="flaky")
    r = client.post("/tasks/dispatch-reminders", headers=TASK).json()
    assert r["failed"] == 1
    row = _queue()[0]
    assert row["status"] == "pending" and "smtp down" in row["last_error"]
    # Backoff: a failed row is not retried on the very next tick.
    send_at = row["send_at"].replace(tzinfo=timezone.utc) if row["send_at"].tzinfo is None else row["send_at"]
    assert send_at > datetime.now(timezone.utc), "failed reminder should be pushed back, not retried immediately"


def test_stale_reminders_for_past_exams_are_not_sent(client, sent_mail):
    """If dispatch was down for days (e.g. the API host was suspended), the
    backlog must not blast 'your exam is in 2 hours' about exams already over."""
    past_exam = {**future(-3), "stage": "final", "lead_minutes": 120, "kind": "exam"}
    _enqueue(datetime.now(timezone.utc) - timedelta(days=3, hours=2), payload=past_exam, key="stale")
    r = client.post("/tasks/dispatch-reminders", headers=TASK).json()
    assert sent_mail == [], sent_mail
    assert _queue()[0]["status"] == "cancelled"


def test_concurrent_dispatch_never_double_sends(client, sent_mail):
    now = datetime.now(timezone.utc)
    for i in range(40):
        _enqueue(now - timedelta(minutes=1), key=f"c{i}")

    results, errors = [], []

    def run():
        try:
            results.append(client.post("/tasks/dispatch-reminders", headers=TASK).json()["sent"])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert sum(results) == 40
    assert len(sent_mail) == 40
    assert all(r["status"] == "sent" for r in _queue())


# ──────────────────────────────── "done" links ────────────────────────────────

def _done_token(item=None, **kw):
    item = item or {"course_code": "CSC201", "date": future(20)["date"], "time": "09:00", "kind": "exam"}
    return done_tokens.make_token(destination="student@uni.edu", item=item, **kw)


def test_done_link_get_does_not_cancel(client, sent_mail):
    _email(client, [future(20)])
    before = [r["status"] for r in _queue()]
    exam = future(20)
    token = _done_token({**exam, "stable_key": None})
    r = client.get(f"/reminders/done?token={token}")
    assert r.status_code == 200 and "stop reminding me" in r.text
    assert [r["status"] for r in _queue()] == before


def test_done_link_post_cancels_that_item_only(client, sent_mail):
    _email(client, [future(20), future(21, course_code="MTH211")])
    token = _done_token({"course_code": "CSC201", "date": future(20)["date"], "time": "09:00", "kind": "exam"})
    r = client.post("/reminders/done", data={"token": token})
    assert r.status_code == 200 and "stopped" in r.text
    rows = _queue()
    csc = [r for r in rows if "CSC201" in r["dedupe_key"]]
    mth = [r for r in rows if "MTH211" in r["dedupe_key"]]
    assert csc and all(r["status"] == "cancelled" for r in csc)
    assert mth and all(r["status"] == "pending" for r in mth)


@pytest.mark.parametrize("mutate", ["tamper", "expired", "garbage"])
def test_done_link_rejects_bad_tokens(client, mutate):
    if mutate == "tamper":
        payload, sig = _done_token().split(".")
        token = payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB") + "." + sig
    elif mutate == "expired":
        token = _done_token(expires_at=1)
    else:
        token = "abc.def"
    r = client.post("/reminders/done", data={"token": token})
    assert "didn" in r.text  # "That link didn't work"


def test_done_page_escapes_html(client):
    token = _done_token({"course_code": "<script>alert(1)</script>", "date": "2030-01-01", "time": "09:00"})
    r = client.get(f"/reminders/done?token={token}")
    assert "<script>alert(1)</script>" not in r.text


# ─────────────────────────────── Google Calendar ──────────────────────────────

def _connect_google(client, monkeypatch, sub="user-1"):
    """Run the whole OAuth dance with Google's token endpoint stubbed out."""
    from google_auth_oauthlib.flow import Flow

    captured = {}

    def fake_fetch_token(self, **kwargs):
        captured.update(kwargs)
        # Mirror Google: a PKCE flow without the verifier is rejected.
        if "code_challenge=" in captured.get("_auth_url", "x") and not kwargs.get("code_verifier"):
            raise ValueError("(invalid_grant) Missing code verifier.")
        import time as _t
        self.oauth2session.token = {"access_token": "at", "refresh_token": "rt", "token_type": "Bearer", "expires_in": 3600, "expires_at": _t.time() + 3600}
        return self.oauth2session.token

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)

    r = client.get("/auth/google", headers=auth_header(sub=sub))
    assert r.status_code == 200, r.text
    start_url = r.json()["auth_url"]
    r = client.get(start_url, follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    google_url = r.headers["location"]
    captured["_auth_url"] = google_url
    state = parse_qs(urlparse(google_url).query)["state"][0]
    r = client.get(f"/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    return r, captured, google_url


def test_google_oauth_callback_completes(client, monkeypatch):
    r, captured, google_url = _connect_google(client, monkeypatch)
    assert r.status_code in (302, 307), r.text
    assert "google_connected=true" in r.headers["location"]
    if "code_challenge=" in google_url:
        assert captured.get("code_verifier"), "PKCE challenge sent but verifier never supplied"


def test_google_callback_rejects_forged_state(client):
    client.cookies.set("oauth_state", "real")
    r = client.get("/auth/google/callback?code=abc&state=forged", follow_redirects=False)
    assert r.status_code == 400


def test_google_connection_belongs_to_the_signed_in_user(client, monkeypatch):
    """Tokens must follow the Supabase user, not a third-party cookie that
    Safari and Chrome's tracking protection drop on cross-site requests."""
    _connect_google(client, monkeypatch, sub="user-1")
    client.cookies.clear()  # the browser refused the cross-site cookie
    assert client.get("/auth/google/status", headers=auth_header(sub="user-1")).json()["connected"] is True
    assert client.get("/auth/google/status", headers=auth_header(sub="user-2")).json()["connected"] is False


class FakeCalendar:
    """Just enough of the Google Calendar API to exercise sync."""

    def __init__(self):
        self.events = {}
        self.n = 0

    def events_api(self):
        return self

    # service.events() returns this object
    def list(self, calendarId, privateExtendedProperty, maxResults=None):
        key = privateExtendedProperty.split("=", 1)[1]
        items = [dict(id=i, **e) for i, e in self.events.items() if e["extendedProperties"]["private"]["xamioKey"] == key]
        return _Exec({"items": items[:maxResults] if maxResults else items})

    def insert(self, calendarId, body):
        self.n += 1
        eid = f"e{self.n}"
        self.events[eid] = body
        return _Exec({"id": eid})

    def update(self, calendarId, eventId, body):
        self.events[eventId] = body
        return _Exec({"id": eventId})

    def delete(self, calendarId, eventId):
        self.events.pop(eventId, None)
        return _Exec({})


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


def test_google_sync_is_idempotent_and_cleans_up(client, monkeypatch):
    from services import google_calendar

    _connect_google(client, monkeypatch)
    cal = FakeCalendar()
    monkeypatch.setattr(google_calendar, "build", lambda *a, **k: type("S", (), {"events": lambda self: cal})())

    exams = [future(20), future(21, course_code="MTH211")]
    body = {"exams": exams, "timezone": "Africa/Lagos"}
    r = client.post("/sync/google", json=body, headers=auth_header())
    assert r.status_code == 200, r.text
    assert len(cal.events) == 2
    for ev in cal.events.values():
        assert len(ev["reminders"]["overrides"]) <= 5

    # Same schedule again: updates in place.
    client.post("/sync/google", json=body, headers=auth_header())
    assert len(cal.events) == 2

    # MTH211 moved a day later: the old event must go, not linger as a phantom.
    from models import ExamEntry

    old_key = ExamEntry(**exams[1]).stable_key()
    moved = [exams[0], future(22, course_code="MTH211")]
    r = client.post("/sync/google", json={**body, "exams": moved, "stale_keys": [old_key]}, headers=auth_header())
    assert r.status_code == 200, r.text
    dates = sorted(ev["start"]["dateTime"][:10] for ev in cal.events.values())
    assert dates == sorted([exams[0]["date"], moved[1]["date"]])


def test_google_sync_without_connection(client):
    r = client.post("/sync/google", json={"exams": [future(20)]}, headers=auth_header())
    assert r.status_code == 401


# ─────────────────────────── abuse resistance ───────────────────────────

def test_rate_limit_cannot_be_dodged_with_fake_client_addresses(client):
    """One account rotating X-Forwarded-For must still be limited."""
    codes = [
        upload(client, "t.txt", samples.as_txt(), headers={**auth_header(), "X-Forwarded-For": f"203.0.113.{i}"}).status_code
        for i in range(6)
    ]
    assert codes[:5] == [200] * 5 and codes[5] == 429, codes


def test_rate_limit_is_per_user(client):
    for sub in ("alice", "bob"):
        codes = [upload(client, "t.txt", samples.as_txt(), headers=auth_header(sub=sub)).status_code for _ in range(5)]
        assert codes == [200] * 5, (sub, codes)


def test_email_refused_when_there_is_no_verified_address(client, sent_mail, monkeypatch):
    """With auth unconfigured the API fails open; it must not become an open
    relay that mails any address a request names."""
    from services import auth

    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", None)
    monkeypatch.setattr(auth, "SUPABASE_URL", None)
    r = client.post("/alerts/email", json={"email": "victim@example.com", "exams": [future(20)]})
    assert r.status_code == 503, r.text
    assert sent_mail == [] and _queue() == []
