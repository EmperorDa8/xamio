# timezone aliased: build_ics has a `timezone: str` parameter that would
# otherwise shadow the datetime module's timezone.
from datetime import datetime, timedelta, timezone as dt_timezone
from icalendar import Calendar, Event, Alarm
from models import ExamEntry
from services.datetime_utils import parse_exam_datetime_in_timezone
from services import reminder_policy


# A deadline is a moment, but a zero-length event renders inconsistently across
# calendar apps (some hide it entirely). A short block is the reliable way to
# show an instant.
DEADLINE_EVENT_MINUTES = 15

# What the event is called in the calendar. Shouted, because these compete for
# attention with everything else in a student's week.
KIND_PREFIX = {
    "exam": "EXAM",
    "test": "TEST",
    "coursework": "DUE",
    "problem_set": "DUE",
    "milestone": "MILESTONE",
    "meeting": "MEETING",
}


def build_ics(
    exams: list[ExamEntry],
    reminder_minutes: list[int] | None = None,
    timezone: str = "UTC",
    mode: str = "smart",
) -> bytes:
    """Render the schedule as an .ics.

    Alarms are chosen per event from the item's kind, so a downloaded calendar
    carries the same ladder as the emails rather than one blanket lead time
    applied to an exam and an essay alike.
    """
    cal = Calendar()
    cal.add("prodid", "-//Exam Timer//exam-timer//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "My Exam Schedule")

    for exam in exams:
        dt_start = parse_exam_datetime_in_timezone(exam.date, exam.time, timezone)
        if dt_start is None:
            continue  # skip rows with unrecoverable date/time rather than failing the batch

        # A sitting occupies a block of time; a deadline is a moment. Giving a
        # submission a two-hour block would blank out the student's afternoon in
        # their calendar for something that takes an instant to hand in.
        if exam.is_attended:
            dt_end = dt_start + timedelta(minutes=exam.duration_minutes or 120)
        else:
            dt_end = dt_start + timedelta(minutes=DEADLINE_EVENT_MINUTES)

        event = Event()
        # Deterministic UID: re-importing the same schedule updates events
        # in place rather than creating duplicates in the calendar app.
        event.add("uid", f"{exam.stable_key()}@xamio.app")
        event.add("dtstamp", datetime.now(dt_timezone.utc))

        event.add("dtstart", dt_start)
        event.add("dtend", dt_end)
        event.add("summary", f"{KIND_PREFIX[exam.kind]}: {exam.display_name()}")

        description_parts = []
        if exam.course_name:
            description_parts.append(f"Course: {exam.course_name}")
        if exam.venue:
            description_parts.append(f"Venue: {exam.venue}")
        if exam.is_attended:
            description_parts.append(f"Duration: {exam.duration_minutes or 120} minutes")
        if exam.weight_pct is not None:
            description_parts.append(f"Worth: {exam.weight_pct:g}% of the final grade")
        if exam.submission_url:
            description_parts.append(f"Submit: {exam.submission_url}")
        event.add("description", "\n".join(description_parts))

        if exam.venue:
            event.add("location", exam.venue)

        for minutes in reminder_policy.minutes_for(exam, mode, reminder_minutes):
            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", f"{KIND_PREFIX[exam.kind]}: {exam.display_name()}")
            alarm.add("trigger", timedelta(minutes=-minutes))
            event.add_component(alarm)

        cal.add_component(event)

    return cal.to_ical()
