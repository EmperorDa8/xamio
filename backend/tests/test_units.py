"""Focused tests for logic the endpoint tests can't reach directly."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from models import ExamEntry
from services import parser, reminder_queue

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def exam(code, name=None):
    return ExamEntry(course_code=code, course_name=name, date="2026-10-10", time="09:00")


# ─── course matching (the AI path returns every course on the sheet) ───

def test_match_is_exact_on_codes():
    exams = [exam("SENG101"), exam("ENG101"), exam("ENG1011"), exam("CSC201")]
    matched, unmatched = parser.match_exams(exams, ["ENG 101"])
    assert [e.course_code for e in matched] == ["ENG101"]
    assert unmatched == []


def test_match_accepts_code_with_name_and_punctuation():
    matched, _ = parser.match_exams([exam("CSC-201"), exam("CSC202")], ["csc 201 - Data Structures"])
    assert [e.course_code for e in matched] == ["CSC-201"]


def test_match_by_name_when_no_code_given():
    exams = [exam("CSC201", "Data Structures"), exam("CSC202", "Algorithms")]
    matched, unmatched = parser.match_exams(exams, ["Data Structures", "Quantum Basket Weaving"])
    assert [e.course_code for e in matched] == ["CSC201"]
    assert unmatched == ["Quantum Basket Weaving"]


def test_short_names_do_not_match_everything():
    matched, _ = parser.match_exams([exam("CSC201", "Data Structures")], ["a"])
    assert matched == []


# ─── stale-reminder triage ───

def row(payload, send_at):
    return {"id": 1, "payload": payload, "send_at": send_at}


def test_triage_sends_an_on_time_reminder():
    p = {"date": "2026-10-02", "time": "09:00", "lead_minutes": 1440,
         "due_at": "2026-10-02T08:00:00+00:00"}
    assert reminder_queue.triage(row(p, NOW - timedelta(minutes=2)), NOW)[0] == "send"


def test_triage_cancels_reminder_for_an_exam_already_over():
    p = {"date": "2026-09-28", "time": "09:00", "lead_minutes": 120,
         "due_at": "2026-09-28T08:00:00+00:00"}
    action, why = reminder_queue.triage(row(p, NOW - timedelta(days=3)), NOW)
    assert action == "cancel" and "expired" in why


def test_triage_cancels_a_rung_that_is_far_too_late():
    # "Two weeks to go" arriving with four days left.
    p = {"date": "2026-10-05", "time": "09:00", "lead_minutes": 20160,
         "due_at": "2026-10-05T08:00:00+00:00"}
    action, why = reminder_queue.triage(row(p, NOW - timedelta(days=10)), NOW)
    assert action == "cancel" and "superseded" in why


def test_triage_tolerates_small_delays():
    p = {"date": "2026-10-01", "time": "14:00", "lead_minutes": 120,
         "due_at": "2026-10-01T13:00:00+00:00"}
    assert reminder_queue.triage(row(p, NOW - timedelta(minutes=20)), NOW)[0] == "send"


def test_triage_legacy_row_without_due_at_uses_a_safe_bound():
    # Queued before due_at existed: no timezone, so only cancel once the
    # exam is over everywhere on Earth.
    over = {"date": "2026-09-29", "time": "09:00", "lead_minutes": 120}
    maybe = {"date": "2026-10-01", "time": "09:00", "lead_minutes": 120}
    assert reminder_queue.triage(row(over, NOW - timedelta(hours=1)), NOW)[0] == "cancel"
    assert reminder_queue.triage(row(maybe, NOW - timedelta(minutes=30)), NOW)[0] == "send"


def test_triage_trims_expired_items_from_a_digest():
    p = {"digest": True, "items": [
        {"course_code": "A", "date": "2026-09-20", "time": "09:00", "due_at": "2026-09-20T08:00:00+00:00"},
        {"course_code": "B", "date": "2026-10-09", "time": "09:00", "due_at": "2026-10-09T08:00:00+00:00"},
    ]}
    action, payload = reminder_queue.triage(row(p, NOW - timedelta(minutes=1)), NOW)
    assert action == "send"
    assert [e["course_code"] for e in payload["items"]] == ["B"]


def test_triage_cancels_a_fully_expired_digest():
    p = {"digest": True, "items": [{"course_code": "A", "date": "2026-09-20", "time": "09:00"}]}
    assert reminder_queue.triage(row(p, NOW - timedelta(days=9)), NOW)[0] == "cancel"


# ─── frontend/backend key parity ───

FRONTEND_KEY = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "stableKey.js"

CASES = [
    {"course_code": "CSC 201", "date": "2026-10-10", "kind": "exam"},
    {"course_code": "csc-201", "date": "2026-10-10"},
    {"course_code": "ENG203", "date": "2026-10-12", "kind": "coursework", "title": "Essay 2"},
    {"course_code": "ENG203", "date": "2026-10-12", "kind": "coursework", "title": "  Essay 3 "},
    {"course_code": "PHY101", "date": "2026-11-01", "kind": "meeting", "title": None},
    {"course_code": "BIO1", "date": "", "kind": "problem_set", "title": "Week 4"},
]


@pytest.mark.skipif(not FRONTEND_KEY.exists(), reason="frontend key module missing")
def test_stable_key_matches_frontend_byte_for_byte():
    script = (
        f"import {{ stableKey }} from {json.dumps(FRONTEND_KEY.as_uri())};"
        f"const cases = {json.dumps(CASES)};"
        "Promise.all(cases.map(stableKey)).then(k => console.log(JSON.stringify(k)));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    py = [
        ExamEntry(time="09:00", **{k: v for k, v in c.items() if v is not None or k == "title"}).stable_key()
        for c in CASES
    ]
    assert js == py


# ─── same-day sittings ───

def test_morning_and_afternoon_papers_are_not_flagged():
    exams = [
        ExamEntry(course_code="CSC201", date="2026-10-10", time="09:00", duration_minutes=120),
        ExamEntry(course_code="MTH211", date="2026-10-10", time="13:00", duration_minutes=120),
    ]
    assert parser.check_duplicate_dates(exams) == []
    assert all(e.date_verified is None for e in exams)


def test_overlapping_sittings_are_flagged():
    exams = [
        ExamEntry(course_code="CSC201", date="2026-10-10", time="09:00", duration_minutes=180),
        ExamEntry(course_code="MTH211", date="2026-10-10", time="11:00", duration_minutes=120),
    ]
    warnings = parser.check_duplicate_dates(exams)
    assert len(warnings) == 1 and "overlap" in warnings[0]
    assert all(e.date_verified is False for e in exams)


# ─── branded HTML emails ───

from services import email_templates


def test_reminder_html_escapes_parsed_text():
    html = email_templates.reminder_html(
        {"course_code": "CSC<b>", "title": "<script>x</script>", "kind": "coursework",
         "stage": "start", "date": "2026-10-10", "time": "23:59", "lead_minutes": 2880}
    )
    assert "<script>x" not in html and "&lt;script&gt;" in html
    assert "in 2 days" in html
    # the example slot from the action copy is drawn as a fill-in chip
    assert "Thu 09:00-10:00, desk - outline + intro" in html


def test_digest_and_summary_html_list_every_item():
    items = [
        {"course_code": "MTH211", "kind": "exam", "date": "2026-10-12", "time": "09:00"},
        {"course_code": "CSC201", "kind": "problem_set", "date": "2026-10-11", "time": "17:00"},
    ]
    digest = email_templates.digest_html({"items": items})
    assert "2 things" in digest and "Start with CSC201" in digest
    summary = email_templates.summary_html(items, "deadline")
    assert "MTH211" in summary and "CSC201" in summary and "Sun 11 Oct" in summary
