import hashlib
import re
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Optional


# What kind of thing a row is. This is the one piece of metadata no competitor
# has — Canvas and Moodle only know "an item with a date", so they cannot pick a
# sensible reminder schedule and have to make the student configure every item
# by hand. Because we parse the source document, we can tell an exam from a
# weekly problem set, and a weekly problem set needs a completely different
# reminder pattern from a dissertation chapter.
#
#   exam        sit-down assessment; you attend at a time and place
#   test        shorter in-class test / quiz; same shape, lower stakes
#   coursework  essay, report, project — a DEADLINE you submit by
#   problem_set recurring weekly homework; individually low-stakes, high volume
#   milestone   postgrad: thesis chapter, ethics submission, grant/CFP deadline
#   meeting     supervisor meeting, viva, defence — the value is the prep
ItemKind = Literal["exam", "test", "coursework", "problem_set", "milestone", "meeting"]

# Kinds you physically attend, so two of them at once is a real clash and a
# `duration_minutes` is meaningful. Everything else is a deadline: several due
# the same day is completely normal and must never be reported as a conflict.
ATTENDED_KINDS: frozenset[str] = frozenset({"exam", "test", "meeting"})


class ExamEntry(BaseModel):
    course_code: str
    course_name: Optional[str] = None
    date: str  # ISO format: YYYY-MM-DD
    time: str  # HH:MM
    duration_minutes: Optional[int] = 120
    venue: Optional[str] = None
    # Defaults to "exam" so every schedule stored or in flight before this field
    # existed keeps its old meaning and behaviour.
    kind: ItemKind = "exam"
    title: Optional[str] = None  # "Essay 2: Coastal erosion" — a deadline needs a name

    # Only meaningful for deadline kinds; all optional because most source
    # documents don't state them.
    weight_pct: Optional[float] = None  # share of the final grade
    est_effort_hours: Optional[float] = None  # how long the work itself takes
    submission_url: Optional[str] = None  # where it gets handed in

    @property
    def is_attended(self) -> bool:
        """True for things you turn up to, False for things you submit."""
        return self.kind in ATTENDED_KINDS

    def display_name(self) -> str:
        """What to call this row in an email, a calendar entry or the UI."""
        label = self.title or self.course_name
        return f"{self.course_code} — {label}" if label else self.course_code
    # Set by the date verifier (services.parser.verify_and_correct_dates):
    #   True  -> date confirmed against the uploaded timetable
    #   False -> could not be confirmed; needs the user's attention
    #   None  -> not checked
    date_verified: Optional[bool] = None
    date_note: Optional[str] = None  # human-readable explanation when not a clean match

    def stable_key(self) -> str:
        """Stable identity for calendar exports: same course+date -> same key,
        so a re-export UPDATES the existing event instead of duplicating it
        (used as the ICS UID and as the Google event tag).

        Course code + date alone stopped being unique once a row could be
        something other than an exam: an essay due on the day of that course's
        exam would collide and overwrite it. The kind is therefore part of the
        key — but it is appended ONLY for non-exams, so every key already in a
        user's Google Calendar keeps hashing to exactly the same value. Folding
        "exam" in unconditionally would change every existing key and duplicate
        every event already synced.
        """
        code = re.sub(r"[^a-z0-9]", "", (self.course_code or "").lower())
        basis = f"{code}|{self.date}"
        if self.kind != "exam":
            # Title included so two coursework deadlines for the same course on
            # the same day stay distinct.
            basis += f"|{self.kind}|{(self.title or '').strip().lower()}"
        return hashlib.sha1(basis.encode()).hexdigest()


class ParsedTimetable(BaseModel):
    model_config = ConfigDict(protected_namespaces=())  # allow the "model_used" field

    exams: list[ExamEntry]
    raw_text: Optional[str] = None
    registered_courses: list[str] = Field(default_factory=list)
    unmatched_courses: list[str] = Field(default_factory=list)
    model_used: Optional[str] = None  # which LLM served this parse
    date_warnings: list[str] = Field(default_factory=list)  # date checks that need review


# "smart" derives each item's reminders from its kind (services/reminder_policy);
# "custom" applies the caller's reminder_minutes unchanged to every item.
# Smart is the default because a student who never opens a settings screen is
# exactly the one who misses deadlines.
ReminderMode = Literal["smart", "custom"]


class SyncRequest(BaseModel):
    exams: list[ExamEntry]
    reminder_minutes: list[int] = Field(default_factory=lambda: [1440, 180])  # 1 day + 3 hours before
    reminder_mode: ReminderMode = "smart"
    timezone: str = "UTC"  # IANA tz from the browser, e.g. "Africa/Lagos"
    # stable_key()s of exams that no longer exist (dropped courses, or the OLD
    # date of an exam the university moved). Their calendar events are deleted
    # on sync — otherwise the old date lingers as a phantom exam, because the
    # key is derived from course code + date and a move produces a new one.
    stale_keys: list[str] = Field(default_factory=list)


class EmailAlertRequest(BaseModel):
    email: str
    exams: list[ExamEntry]
    reminder_minutes: list[int] = Field(default_factory=lambda: [1440, 180])
    reminder_mode: ReminderMode = "smart"
    timezone: str = "UTC"


class EmailAlertResult(BaseModel):
    success: bool
    message: str
    scheduled: int = 0
    # Non-blocking schedule notes (e.g. two exams overlapping on one day).
    warnings: list[str] = Field(default_factory=list)


class GoogleAuthResponse(BaseModel):
    auth_url: str


class SyncResult(BaseModel):
    success: bool
    message: str
    event_ids: list[str] = Field(default_factory=list)
    # Non-blocking schedule notes (e.g. two exams overlapping on one day).
    warnings: list[str] = Field(default_factory=list)
