"""How many reminders a student may actually receive, and when.

reminder_policy decides what each item deserves in isolation. It cannot know
that the student has nine other things that Tuesday — so on its own it scales
badly in exactly the case that matters: fifteen assignments times five rungs is
seventy-five emails, which is not a reminder system, it is a reason to filter us
into a folder.

This module is the layer that knows about the whole week. It does two things:

1. QUIET HOURS. A reminder that arrives at 03:00 is worse than no reminder: it
   wakes someone, teaches them we are careless, and gets notifications turned
   off for good. Google Classroom mailing "due today" at midnight is the
   canonical version of this mistake.

2. CAPS AND DIGESTS. Beyond a small number of messages a day, each additional
   one makes every other one less likely to be read. Rather than send them
   anyway or silently drop them, the overflow is collapsed into a single daily
   digest — batching, which the interruption research supports, rather than
   deletion. Nothing is lost; it is combined.

The caps are deliberately not user-settings. A student cannot know in September
how noisy December will be, and the failure mode is silent (they stop reading,
they do not complain). Treating the cap as a product constraint rather than a
preference is the whole point.

Applies to EMAIL only. Calendar alarms are relative triggers the calendar app
fires itself ("30 minutes before"), so we cannot move them into daylight or
count them — another reason the .ics is a good quiet channel for the student who
has muted everything else.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Iterable, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models import ExamEntry
from services.reminder_policy import Rung


# Local hours during which nothing is sent. Inclusive of the start, exclusive of
# the end: 22:00 is quiet, 07:00 is not.
QUIET_START_HOUR = 22
QUIET_END_HOUR = 7

# Individually delivered emails per local day. Everything above this on the same
# day is merged into that day's digest.
MAX_INDIVIDUAL_PER_DAY = 2

# Emails per ISO week, counting digests. This is a TOTAL, not a budget for
# individual sends with digests added on top — the student experiences what
# lands in the inbox, so that is what has to be bounded. Capping only the
# individual sends let a heavy week reach fifteen emails, which is the failure
# this module exists to prevent.
#
# Known limit: the cap holds within one planning run and across repeats of the
# same run, but a SEPARATE later upload whose reminders land in an already-full
# week can add one more email to it. The alternative — refusing to schedule
# anything for the new items, or silently moving their digest to a day after
# they are due — is worse than one extra email, so this is a deliberate choice
# rather than an oversight.
MAX_PER_WEEK = 10

# When the daily digest goes out, local time. Early evening: late enough to
# cover the day, early enough to still act on tomorrow.
DIGEST_HOUR = 18

# Which reminders keep an individual slot when a day is oversubscribed. Lower
# wins. Imminent and high-stakes beat far-off and general — but "start" ranks
# high deliberately, because it is the rung that changes behaviour rather than
# just informing, and it is the one no competitor sends at all.
STAGE_PRIORITY = {
    "logistics": 0,
    "final": 1,
    "start": 2,
    "prep": 3,
    "near": 4,
    "revision": 5,
}
_DEFAULT_PRIORITY = 9


class Candidate(NamedTuple):
    """One reminder we are considering sending."""

    item: ExamEntry
    rung: Rung
    send_at: datetime  # UTC
    due_at: datetime  # UTC — the moment the item actually happens


class Plan(NamedTuple):
    """The decision for one batch."""

    individual: list[Candidate]
    # local date -> the candidates merged into that day's single digest
    digests: dict[date, list[Candidate]]

    @property
    def total_sends(self) -> int:
        return len(self.individual) + len(self.digests)


def _zone(timezone: str) -> ZoneInfo:
    """Resolve an IANA identifier, falling back to UTC.

    Deliberately an identifier and never a fixed offset: an offset would drift
    by an hour at every DST transition and silently move every future reminder
    for students in countries that observe it.
    """
    try:
        return ZoneInfo(timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_day(moment: datetime, timezone: str) -> date:
    """Which calendar day a moment falls on for this student."""
    return moment.astimezone(_zone(timezone)).date()


def _is_quiet(local_dt: datetime) -> bool:
    return local_dt.hour >= QUIET_START_HOUR or local_dt.hour < QUIET_END_HOUR


def apply_quiet_hours(send_at: datetime, due_at: datetime, timezone: str) -> datetime:
    """Move a send time out of the small hours, in the student's own timezone.

    Direction matters. Moving a reminder LATER risks pushing it past the thing
    it is reminding about, which turns a useful message into a taunt. So we
    prefer to move forward to the end of quiet hours only when that is still
    safely before the deadline, and otherwise fall back to moving EARLIER, to
    just before quiet hours began. A reminder that arrives early still works; a
    reminder that arrives late does not.
    """
    zone = _zone(timezone)
    local = send_at.astimezone(zone)
    if not _is_quiet(local):
        return send_at

    # Forward: 07:00 on the morning this quiet stretch ends.
    morning_day = local.date() if local.hour < QUIET_END_HOUR else local.date() + timedelta(days=1)
    forward = datetime.combine(morning_day, time(QUIET_END_HOUR), tzinfo=zone).astimezone(
        dt_timezone.utc
    )
    if forward < due_at:
        return forward

    # Backward: the last minute before quiet hours started.
    evening_day = local.date() - timedelta(days=1) if local.hour < QUIET_END_HOUR else local.date()
    backward = datetime.combine(
        evening_day, time(QUIET_START_HOUR - 1, 0), tzinfo=zone
    ).astimezone(dt_timezone.utc)
    return backward


def _sort_key(c: Candidate) -> tuple:
    return (STAGE_PRIORITY.get(c.rung.stage, _DEFAULT_PRIORITY), c.send_at)


def plan_sends(
    candidates: Iterable[Candidate],
    timezone: str,
    existing_send_times: Iterable[datetime] = (),
) -> Plan:
    """Split candidates into individual sends and per-day digests.

    `existing_send_times` are the send times of reminders ALREADY queued for
    this student. Without them, uploading a second timetable would start the
    budget from zero and quietly double what lands in their inbox.

    Planning is deliberately a pure function of the candidates plus the OTHER
    traffic already queued. The caller excludes this batch's own rows from
    `existing_send_times`, so re-running an unchanged schedule reproduces the
    same plan rather than treating its own previous output as competition.
    """
    zone = _zone(timezone)

    def local_day(dt: datetime) -> date:
        return dt.astimezone(zone).date()

    def week_of(d: date) -> tuple[int, int]:
        iso = d.isocalendar()
        return (iso[0], iso[1])

    # Reminders already queued spend budget before anything new is considered.
    day_used: dict[date, int] = {}
    week_used: dict[tuple[int, int], int] = {}
    for dt in existing_send_times:
        d = local_day(dt)
        day_used[d] = day_used.get(d, 0) + 1
        week_used[week_of(d)] = week_used.get(week_of(d), 0) + 1

    individual: list[Candidate] = []
    digests: dict[date, list[Candidate]] = {}
    overflow: list[Candidate] = []

    # Pass 1, by VALUE. The rungs that keep an individual slot should be the
    # ones worth interrupting someone for, so this runs in priority order.
    #
    # One slot a week is held back for a digest. Without the reservation a week
    # could spend its whole budget on individual sends and then have nowhere
    # legal to put the overflow.
    individual_budget = MAX_PER_WEEK - 1
    for c in sorted(candidates, key=_sort_key):
        d = local_day(c.send_at)
        wk = week_of(d)
        if (
            day_used.get(d, 0) < MAX_INDIVIDUAL_PER_DAY
            and week_used.get(wk, 0) < individual_budget
        ):
            individual.append(c)
            day_used[d] = day_used.get(d, 0) + 1
            week_used[wk] = week_used.get(wk, 0) + 1
        else:
            overflow.append(c)

    # Pass 2, by DATE. Chronological order is what makes the merge safe: the
    # first overflow in a week opens a digest on the EARLIEST overflow day, so
    # everything later in that week always has an earlier digest to fall back
    # into. Running this in priority order instead could open the week's only
    # digest on a Sunday and then have to fold Tuesday's reminder into it —
    # arriving days after the thing it was warning about.
    for c in sorted(overflow, key=lambda c: c.send_at):
        d = local_day(c.send_at)
        wk = week_of(d)

        if d in digests:  # joining an existing digest costs nothing
            digests[d].append(c)
            continue

        if week_used.get(wk, 0) < MAX_PER_WEEK:
            digests[d] = [c]
            day_used[d] = day_used.get(d, 0) + 1
            week_used[wk] = week_used.get(wk, 0) + 1
            continue

        # Week is full: fold into the latest digest that still lands before
        # this reminder's own day. Arriving early is survivable; arriving after
        # the deadline is not.
        earlier = sorted(day for day in digests if week_of(day) == wk and day <= d)
        if earlier:
            digests[earlier[-1]].append(c)
        else:
            # Nothing earlier in this week to fold into. A lost reminder is a
            # worse failure than an extra email, so this errs toward sending.
            digests[d] = [c]
            week_used[wk] = week_used.get(wk, 0) + 1

    return Plan(individual=individual, digests=digests)


def digest_send_time(day: date, timezone: str, members: list[Candidate]) -> datetime:
    """When a given day's digest goes out, in UTC.

    Normally early evening. But a digest must never arrive after the earliest
    thing it is telling you about, so if something in it is due before then, it
    moves ahead of that instead.

    It always stays INSIDE `day`. plan_sends counted this digest against that
    day's and that week's budget, so letting it drift onto the previous evening
    would quietly put a week over its cap — which is how the first version of
    this leaked an extra email into the busiest week. The day's 07:00 floor
    keeps it out of quiet hours without needing to move days.
    """
    zone = _zone(timezone)
    preferred = datetime.combine(day, time(DIGEST_HOUR), tzinfo=zone)
    floor = datetime.combine(day, time(QUIET_END_HOUR), tzinfo=zone)

    earliest_due = min((m.due_at for m in members), default=None)
    if earliest_due and preferred.astimezone(dt_timezone.utc) >= earliest_due:
        # An hour of runway ahead of the earliest item, never earlier than the
        # day's floor.
        ahead = (earliest_due - timedelta(hours=1)).astimezone(zone)
        return max(ahead, floor).astimezone(dt_timezone.utc)
    return preferred.astimezone(dt_timezone.utc)
