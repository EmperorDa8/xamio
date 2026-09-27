"""What a reminder actually says.

A reminder that repeats the due date tells the student nothing they did not
already know — the deadline was never the missing information. What changes
behaviour is naming a specific action and a specific slot to do it in.

That is the implementation-intention finding: Gollwitzer & Sheeran's
meta-analysis (94 studies, ~8,000 participants) put forming a "when and where"
plan at d = 0.65 on goal attainment, over and above already holding the goal.
The commercial version of the same effect is that contextual push notifications
open at roughly 14% against 4% for generic ones. It is the cheapest change
available here: identical send volume, materially different message.

An honest limit: the effect comes from the STUDENT forming the plan, not from
an app asserting one. We cannot know where someone studies or when they are
free, so these messages do the one thing software can do — name a concrete next
action, and prompt for the slot in a fillable shape. That is weaker than the
lab paradigm and should not be oversold.

Two deliberate constraints:
  - No marketing, ever. Under CAN-SPAM these are transactional messages; adding
    promotional content reclassifies the whole email and drags it into rules it
    currently sits outside. A plan upsell in a deadline reminder is a legal
    problem, not just a taste one.
  - Nothing invented. A message only states a venue, link or weighting when the
    parsed document actually gave us one.
"""
from __future__ import annotations

import os

from services import done_tokens

HOUR = 60
DAY = 24 * HOUR

# Above this many lines, per-item "done" links turn a digest into a wall of
# URLs, so they are dropped.
MAX_DIGEST_DONE_LINKS = 5


def humanize_lead(minutes: int | None) -> str:
    """"in 2 weeks", "tomorrow", "in 3 hours" — how far out this reminder is.

    Phrased from the reader's position rather than as a raw offset: "1440
    minutes before" is data, "tomorrow" is a cue to act.
    """
    if not minutes or minutes <= 0:
        return "now"
    if minutes < HOUR:
        return f"in {minutes} minutes"
    if minutes < DAY:
        hours = round(minutes / HOUR)
        return "in an hour" if hours == 1 else f"in {hours} hours"
    days = round(minutes / DAY)
    if days == 1:
        return "tomorrow"
    if days < 7:
        return f"in {days} days"
    if days < 14:
        return "in a week"
    if days < 28:
        return f"in {round(days / 7)} weeks"
    months = round(days / 30)
    return "in a month" if months <= 1 else f"in {months} months"


# (kind, stage) -> (subject suffix, what to do now)
#
# The action line is the whole point. It is written to be specific enough that
# the student could start in the next five minutes without deciding anything
# else first — "decide what to do" is the step people stall on.
_ACTIONS: dict[tuple[str, str], str] = {
    # --- exams: weeks of runway, so early messages are about starting to
    # revise at all, and the last one is pure logistics.
    ("exam", "revision"): (
        "Pick ONE topic and one slot in the next two days, and write it down:\n"
        '  "Wed 10:00-10:45, library - past paper Q1"\n\n'
        "Choosing when and where beats deciding to revise more."
    ),
    ("exam", "final"): (
        "Tonight: 30 minutes on your weakest topic, then stop.\n"
        "Then pack what you need and set an alarm."
    ),
    ("exam", "logistics"): (
        "Leave now if you have not already. Bring your ID and a pen.\n"
        "Nothing you read in the next hour will change the result."
    ),
    ("test", "revision"): (
        "Book 30 minutes today and name what you will cover in it."
    ),
    ("test", "final"): "Tonight: one pass over your notes, then stop.",
    ("test", "logistics"): "Head over now. Bring your ID and a pen.",
    # --- coursework: the start rung is the differentiated one, so it carries
    # the strongest prompt.
    ("coursework", "start"): (
        "Start today, not at the weekend.\n\n"
        "Do the smallest real thing - an outline, or the first 200 words - and\n"
        "book the slot now:\n"
        '  "Thu 09:00-10:00, desk - outline + intro"\n\n'
        "Work like this almost always takes longer than it looks."
    ),
    ("coursework", "near"): (
        "Name your next hour on this and what it produces.\n"
        'Not "work on the essay" - "Tue 14:00, section 2 drafted".'
    ),
    ("coursework", "final"): (
        "Finish and submit tonight if you possibly can.\n"
        "Leave tomorrow as slack for the upload going wrong."
    ),
    ("coursework", "logistics"): (
        "Submit now, even if it is not perfect.\n"
        "More submissions fail in the last hour than at any other time."
    ),
    ("problem_set", "final"): (
        "Most of these take under an hour. Doing it tonight costs you less\n"
        "than carrying it around all day tomorrow."
    ),
    # --- milestones: no weekly rhythm exists to carry these, so the early
    # message is about imposing structure rather than working harder.
    ("milestone", "start"): (
        "Break this into four weekly chunks now, and put the first one in your\n"
        "calendar before you close this email.\n\n"
        "Nothing external will pace this for you."
    ),
    ("milestone", "near"): (
        "What moved since last time, and what is the next concrete piece?\n"
        "Book the slot for it now."
    ),
    ("milestone", "final"): (
        "Two days left. Decide what is genuinely finishable and cut the rest -\n"
        "submitted and imperfect beats late."
    ),
    # --- meetings: the calendar already holds the appointment. The prep is
    # the part we can add.
    ("meeting", "prep"): (
        "Write three lines before this: what moved, what is stuck, and what you\n"
        "need a decision on.\n\n"
        "That is the difference between supervision and a status update."
    ),
    ("meeting", "near"): "Draft your three points today if you have not yet.",
    ("meeting", "logistics"): "Starting soon - have your notes open.",
}

# When a row predates stages, or carries one we have no copy for.
_FALLBACK = {
    "exam": "Check what you still need to cover.",
    "test": "Check what you still need to cover.",
    "coursework": "Decide your next hour on this and what it produces.",
    "problem_set": "Worth getting out of the way tonight.",
    "milestone": "Decide the next concrete piece and book the slot.",
    "meeting": "Note what you want out of it.",
}

# How each kind is described to a student. "Starts" and "due" are not
# interchangeable: telling someone their essay "starts at 23:59" reads as a
# parse error and costs trust in everything else in the message.
KIND_LABEL = {
    "exam": ("Exam", "starts"),
    "test": ("Test", "starts"),
    "coursework": ("Due", "by"),
    "problem_set": ("Due", "by"),
    "milestone": ("Milestone", "by"),
    "meeting": ("Meeting", "starts"),
}


def done_link(entry: dict, to: str | None) -> str | None:
    """A one-click "I've handed this in" URL, or None if unavailable.

    Returns None rather than a broken link when the API base or signing secret
    is missing — a dead link in a reminder is worse than no link, because the
    student clicks it, nothing happens, and they stop trusting the rest.
    """
    base = (os.getenv("PUBLIC_API_URL") or "").rstrip("/")
    if not base or not to or not entry.get("stable_key"):
        return None
    try:
        token = done_tokens.make_token(destination=to, item=entry)
    except done_tokens.TokenError:
        return None
    return f"{base}/reminders/done?token={token}"


def _done_footer(entry: dict, to: str | None) -> str:
    link = done_link(entry, to)
    if not link:
        return ""
    verb = "Sat it" if entry.get("kind") in ("exam", "test") else "Handed it in"
    return f"\n\n{verb} already? Stop these reminders:\n{link}"


def _heading(entry: dict) -> str:
    code = entry.get("course_code") or "Deadline"
    name = entry.get("title") or entry.get("course_name")
    return f"{code} — {name}" if name else code


def action_for(entry: dict) -> str:
    kind = entry.get("kind") or "exam"
    stage = entry.get("stage") or ""
    return _ACTIONS.get((kind, stage)) or _FALLBACK.get(kind, _FALLBACK["exam"])


def _when_line(entry: dict) -> str:
    kind = entry.get("kind") or "exam"
    label, preposition = KIND_LABEL.get(kind, KIND_LABEL["exam"])
    return f"{label} {preposition} {entry.get('date','')} at {entry.get('time','')}"


def _detail_lines(entry: dict) -> list[str]:
    """Facts we actually parsed. Never guessed."""
    out = []
    if entry.get("venue"):
        out.append(f"Where: {entry['venue']}")
    if entry.get("weight_pct") is not None:
        out.append(f"Worth: {entry['weight_pct']:g}% of the final grade")
    if entry.get("submission_url"):
        out.append(f"Submit: {entry['submission_url']}")
    return out


def build_reminder(entry: dict, to: str | None = None) -> tuple[str, str]:
    """Subject and body for one reminder."""
    kind = entry.get("kind") or "exam"
    label, _ = KIND_LABEL.get(kind, KIND_LABEL["exam"])
    noun = "deadline" if kind in ("coursework", "problem_set") else label.lower()
    heading = _heading(entry)

    # Rows queued before lead times were recorded have none. Saying "now" for
    # those would be actively wrong — a two-week-out reminder would announce
    # itself as due — so the timeframe is simply left out.
    raw_lead = entry.get("lead_minutes")
    lead = humanize_lead(raw_lead) if raw_lead else None

    # The subject carries the item and the timeframe, because on a phone that
    # is often the entire message that gets read.
    subject = f"{heading} — {lead}" if lead else f"{heading} — coming up"

    lines = [f"{heading}", f"  {_when_line(entry)}"]
    lines += [f"  {d}" for d in _detail_lines(entry)]
    opener = f"Your {noun} is {lead}." if lead else f"Your {noun} is coming up."
    body = (
        opener
        + "\n\n"
        + "\n".join(lines)
        + "\n\n"
        + action_for(entry)
        + "\n\n— Xamio"
    )
    return subject, body


def build_digest(payload: dict, to: str | None = None) -> tuple[str, str]:
    """Subject and body for a day's batched reminders.

    A digest cannot carry a full action prompt per item without becoming a wall
    of text nobody reads, so each line gets a single short next step and the
    email leads with the one thing worth doing first.
    """
    items = sorted(
        payload.get("items") or [],
        key=lambda e: (e.get("date") or "", e.get("time") or ""),
    )
    count = len(items)
    subject = f"{count} deadline{'s' if count != 1 else ''} coming up"

    blocks = []
    for entry in items:
        detail = _detail_lines(entry)
        block = [f"  • {_heading(entry)}", f"      {_when_line(entry)}"]
        block += [f"      {d}" for d in detail]
        blocks.append("\n".join(block))

    first = items[0] if items else None
    lead_line = ""
    if first:
        # One concrete starting point beats a list with no entry point.
        lead_line = (
            f"Start with {_heading(first)}.\n" + action_for(first) + "\n\n"
        )

    body = (
        f"{count} thing{'s' if count != 1 else ''} to keep an eye on:\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + lead_line
        + "These are grouped into one email so your inbox stays readable.\n\n"
        + "— Xamio"
    )
    return subject, body
