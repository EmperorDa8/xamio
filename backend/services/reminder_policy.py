"""Which reminders an item gets, chosen from what kind of item it is.

This is the part competitors cannot copy without solving ingestion first. Canvas
and Moodle only know "an item with a date", so the only ladder they can offer is
one the student configures by hand — which is why the most-requested Canvas
feature for years has been a 24-hour reminder that is simply ON. Because we
parse the source document we know an exam from a weekly problem set, and those
want opposite treatment: an exam needs a runway measured in weeks, a problem set
needs one ping and silence.

The ladders below are defaults, not settings. A student who never opens a
preferences screen should still get the right pattern, because that is the
population that misses deadlines.

Evidence and its limits:
  - Spacing an exam runway over weeks rather than one night draws on distributed
    practice, one of only two techniques rated "high utility" by Dunlosky et al.
    (2013), with gap sizes loosely following Cepeda et al. (~10-20% of the
    retention interval). Honest caveat: that work is about spacing STUDY
    SESSIONS. Sending a reminder is not the same as producing a study session,
    so the exam ladder is an extrapolation, not a finding.
  - The coursework "start" rung is on firmer ground. Buehler, Griffin & Ross
    (1994) had students predict their theses at 33.9 days and take 55.5, with
    ~70% overrunning their own estimate. So a student's own effort estimate is
    never used raw — it is multiplied before it becomes a start date.
  - Batching the weekly problem sets rather than pinging each one follows the
    notification-batching evidence on inattention and stress, and the blunt
    commercial fact that users abandon apps that push at them several times a
    week.

What is deliberately NOT here: frequency caps, digest collapsing and quiet
hours. Those are global, cross-item concerns — a ladder cannot know that the
student already has nine reminders that Tuesday. They belong to the layer above
this one.
"""
from __future__ import annotations

import math
from typing import NamedTuple

from models import ExamEntry


HOUR = 60
DAY = 24 * HOUR


class Rung(NamedTuple):
    """One reminder on a ladder.

    `minutes` is how long before the item it fires. `stage` says what the
    reminder is FOR, which is what lets the message say something specific
    instead of restating the date — the difference between "essay due Friday"
    and "tomorrow 9-10am: outline and intro".
    """

    minutes: int
    stage: str


# Stages, roughly in the order a student meets them:
#   start      begin the work now, or you will run out of runway
#   revision   a spaced study touchpoint on the way to an exam
#   prep       get ready for a scheduled conversation
#   near       it is close; check your progress
#   final      last full day
#   logistics  where to be / how to hand in, not what to know
LADDERS: dict[str, tuple[Rung, ...]] = {
    # Weeks of runway, tightening as it approaches. The last rung is logistics,
    # not study: at two hours out, the useful message is the room and the time,
    # because nothing learned in that window changes the result.
    "exam": (
        Rung(14 * DAY, "revision"),
        Rung(7 * DAY, "revision"),
        Rung(3 * DAY, "revision"),
        Rung(1 * DAY, "final"),
        Rung(2 * HOUR, "logistics"),
    ),
    # A class test is the same shape as an exam with lower stakes, so it gets a
    # shorter runway. Giving it the full exam ladder is how a reminder system
    # starts feeling like noise.
    "test": (
        Rung(7 * DAY, "revision"),
        Rung(2 * DAY, "revision"),
        Rung(1 * DAY, "final"),
        Rung(1 * HOUR, "logistics"),
    ),
    # The start rung is computed per item (see _start_rung) because it depends
    # on how much work the thing actually is.
    "coursework": (
        Rung(7 * DAY, "near"),
        Rung(2 * DAY, "near"),
        Rung(1 * DAY, "final"),
        Rung(3 * HOUR, "logistics"),
    ),
    # One ping, the evening before. A student with five of these a week would
    # get twenty reminders on any other schedule, and would mute us by week two.
    "problem_set": (Rung(1 * DAY, "final"),),
    # Months of runway: a thesis chapter or an ethics submission has no weekly
    # rhythm to carry it, so the reminders are the only structure there is.
    "milestone": (
        Rung(30 * DAY, "start"),
        Rung(14 * DAY, "near"),
        Rung(7 * DAY, "near"),
        Rung(2 * DAY, "final"),
    ),
    # The meeting is already in their calendar. The value we add is the prep
    # rung — turning up with an agenda is the difference between a useful
    # supervision and a status update.
    "meeting": (
        Rung(3 * DAY, "prep"),
        Rung(1 * DAY, "near"),
        Rung(1 * HOUR, "logistics"),
    ),
}

# Students do not work on one assignment all day, so effort hours spread across
# calendar days at roughly this rate. Deliberately conservative: overestimating
# the daily rate produces a start date that is too late, which is the failure
# that costs a grade.
EFFORT_HOURS_PER_DAY = 3.0

# Applied to the student's own effort estimate before it becomes a start date,
# because that estimate is reliably optimistic (Buehler et al.). This is the
# planning-fallacy correction, and a fixed multiplier is a crude stand-in for
# what should eventually be the individual student's own historical overrun.
PLANNING_FALLACY_MULTIPLIER = 1.5

# Bounds on the computed start date. Below the floor the rung collides with the
# T-7d rung and says nothing new; above the ceiling it fires so far out that it
# is forgotten before it is useful.
MIN_START_DAYS = 3
MAX_START_DAYS = 28

# Used when the document states no effort estimate, which is most of the time.
# Weight is the only signal usually available, and a heavily weighted piece is
# generally a longer one. This is a heuristic, not a measurement.
DEFAULT_START_DAYS = 10
HEAVY_WEIGHT_PCT = 25
HEAVY_WEIGHT_START_DAYS = 14


def _start_rung(item: ExamEntry) -> Rung | None:
    """When to tell the student to BEGIN this piece of work.

    The deadline is rarely the thing students get wrong — the start date is.
    Nothing else in the market sends this, because nothing else knows how big
    the work is.
    """
    effort = item.est_effort_hours
    if effort and effort > 0:
        # Inflate the estimate first, then spread it over days they will
        # realistically work.
        days = math.ceil((effort * PLANNING_FALLACY_MULTIPLIER) / EFFORT_HOURS_PER_DAY)
    elif item.weight_pct is not None and item.weight_pct >= HEAVY_WEIGHT_PCT:
        days = HEAVY_WEIGHT_START_DAYS
    else:
        days = DEFAULT_START_DAYS

    days = max(MIN_START_DAYS, min(days, MAX_START_DAYS))
    return Rung(days * DAY, "start")


def ladder_for(item: ExamEntry) -> list[Rung]:
    """The full reminder ladder for one item, soonest-first.

    An unknown kind falls back to the exam ladder, which is what every row meant
    before kinds existed.
    """
    rungs = list(LADDERS.get(item.kind, LADDERS["exam"]))

    if item.kind == "coursework":
        start = _start_rung(item)
        if start:
            # Only keep the start rung if it is meaningfully earlier than the
            # first fixed rung; otherwise it is a second reminder saying the
            # same thing on nearly the same day.
            if start.minutes > rungs[0].minutes:
                rungs.insert(0, start)

    rungs.sort(key=lambda r: r.minutes, reverse=True)
    return rungs


def minutes_for(
    item: ExamEntry,
    mode: str = "smart",
    custom_minutes: list[int] | None = None,
) -> list[int]:
    """Lead times in minutes for one item.

    "smart" picks from the item's kind — this is the default, and the whole
    point of the feature. "custom" applies the student's own chosen lead times
    unchanged to every item, for the people who want to drive it themselves.
    """
    if mode == "custom" and custom_minutes:
        return sorted(set(custom_minutes), reverse=True)
    return [rung.minutes for rung in ladder_for(item)]


def stages_for(
    item: ExamEntry,
    mode: str = "smart",
    custom_minutes: list[int] | None = None,
) -> list[Rung]:
    """Like minutes_for, but keeps the stage attached.

    Custom lead times carry no stage of their own, so each is labelled by how
    close it is — enough for the message to still say something better than the
    date alone.
    """
    if mode == "custom" and custom_minutes:
        return [
            Rung(m, _stage_for_lead_time(m))
            for m in sorted(set(custom_minutes), reverse=True)
        ]
    return ladder_for(item)


def _stage_for_lead_time(minutes: int) -> str:
    if minutes >= 21 * DAY:
        return "start"
    if minutes >= 2 * DAY:
        return "near"
    if minutes >= 6 * HOUR:
        return "final"
    return "logistics"
