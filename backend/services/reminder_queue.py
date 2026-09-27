"""Durable reminder delivery.

A reminder is a row with a `send_at`, not a job inside a running process. That
distinction is the whole point: the previous APScheduler setup tied delivery to
a process Render's free tier is allowed to stop, and a reminder that came due
while the service slept was permanently discarded once it aged past the
one-hour misfire grace — after the user had been told it was scheduled.

Here nothing is lost. A due row stays `pending` until it is actually delivered,
so a missed window costs latency instead of the reminder. An external heartbeat
(pg_cron -> pg_net -> POST /tasks/dispatch-reminders) drives the sending.
"""
import json
from datetime import datetime, timedelta, timezone as dt_timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    func,
    select,
    text as sql_text,
    update,
)

from models import ExamEntry
from services.datetime_utils import parse_exam_datetime_in_timezone
from services.db import IS_POSTGRES, PRIVATE_SCHEMA, engine, prepare
from services import reminder_budget, reminder_policy

# How many reminders one dispatch run will send. Keeps a single invocation well
# inside any request timeout; the next tick picks up whatever is left.
BATCH_SIZE = 50

# A row claimed but not resolved within this long belonged to a process that
# died mid-send. It goes back on the queue rather than being stranded.
STALLED_AFTER = timedelta(minutes=15)

# A reminder delivered this much later than planned has lost its point (the
# "two weeks to go" nudge arriving the day before). It is cancelled rather than
# sent; the next rung of the ladder still goes out on time.
LATE_FLOOR = timedelta(hours=1)
LATE_FRACTION = 0.5

# Legacy rows carry a local date/time but no timezone. Nowhere on Earth is more
# than 14h ahead of UTC, so date+time+14h is a moment by which the item has
# certainly happened, whatever zone the student was in.
_MAX_TZ_OFFSET = timedelta(hours=14)

_metadata = MetaData(schema=PRIVATE_SCHEMA)

reminder_queue = Table(
    "reminder_queue",
    _metadata,
    # SQLite only auto-assigns rowids for INTEGER PRIMARY KEY, not BIGINT, so
    # the local-dev mirror uses Integer while Postgres keeps bigint.
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("channel", String(16), nullable=False, default="email"),
    Column("destination", Text, nullable=False),
    Column("payload", Text, nullable=False, default="{}"),
    Column("send_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False, default="pending"),
    Column("attempts", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=5),
    Column("last_error", Text),
    Column("dedupe_key", Text, nullable=False, unique=True),
    Column("user_id", String(36)),
    Column("schedule_id", String(36)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("claimed_at", DateTime(timezone=True)),
    Column("sent_at", DateTime(timezone=True)),
)


def init() -> None:
    """Create the schema and table if missing. On Postgres the migration has
    already made them, so this is a no-op there; it is what lets local SQLite
    dev work without running migrations.

    Safe to call repeatedly — it runs at most once per process.
    """
    prepare(_metadata)


def _now() -> datetime:
    return datetime.now(dt_timezone.utc)


# jsonb on Postgres wants a dict; the SQLite mirror stores TEXT. Normalising on
# the way in and out keeps callers from caring which backend they are on.
def _dump(payload: dict) -> str:
    return json.dumps(payload)


def _load(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def enqueue(
    *,
    destination: str,
    payload: dict,
    send_at: datetime,
    dedupe_key: str,
    channel: str = "email",
    user_id: str | None = None,
    schedule_id: str | None = None,
    update_payload: bool = False,
) -> bool:
    """Queue one reminder. Returns True if a row was created or rescheduled.

    Re-running the alerts endpoint must not produce duplicate sends, so the
    dedupe key is unique and a conflict reschedules the existing row — but only
    while it is still pending, so an already-delivered reminder is never
    resurrected.

    `update_payload` also refreshes the row's contents on conflict. Needed for a
    daily digest, whose key is just the day: uploading a second timetable adds
    items to a digest that already exists, and without this the new items would
    be dropped on the floor while the row's send time moved.
    """
    init()
    now = _now()
    values = {
        "channel": channel,
        "destination": destination,
        "payload": _dump(payload),
        "send_at": send_at,
        "status": "pending",
        "attempts": 0,
        "max_attempts": 5,
        "dedupe_key": dedupe_key,
        "user_id": user_id,
        "schedule_id": schedule_id,
        "created_at": now,
    }

    with engine.begin() as conn:
        if IS_POSTGRES:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(reminder_queue).values(**values)
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(reminder_queue).values(**values)

        updates = {"send_at": stmt.excluded.send_at}
        if update_payload:
            updates["payload"] = stmt.excluded.payload
        stmt = stmt.on_conflict_do_update(
            index_elements=["dedupe_key"],
            set_=updates,
            where=reminder_queue.c.status == "pending",
        )
        result = conn.execute(stmt)
        return bool(result.rowcount)


def _individual_key(channel: str, to: str, exam: ExamEntry, minutes: int) -> str:
    """Dedupe key for one (item, lead-time) reminder.

    Course code + date + time could not tell two coursework deadlines for one
    course on one day apart — and because a conflicting key only RESCHEDULES
    the existing row, the second item silently replaced the first instead of
    being queued alongside it. stable_key() separates them.

    Exams keep the original key format exactly: a new format would not match
    the rows already sitting in the queue, so re-running the alerts endpoint
    would queue a second copy of every reminder a user is already waiting on,
    and they would get each one twice.
    """
    if exam.kind == "exam":
        return f"{channel}:{to}:{exam.course_code}:{exam.date}:{exam.time}:{minutes}"
    return f"{channel}:{to}:{exam.stable_key()}:{exam.time}:{minutes}"


def _digest_key(channel: str, to: str, day) -> str:
    return f"{channel}:{to}:digest:{day.isoformat()}"


def _item_identity(entry: dict) -> tuple:
    """Identity of one line inside a digest, for de-duplicating on merge."""
    return (
        entry.get("course_code"),
        entry.get("date"),
        entry.get("time"),
        entry.get("kind"),
        entry.get("title"),
        entry.get("stage"),
    )


def pending_digests(destination: str, channel: str = "email") -> dict[str, dict]:
    """Digest rows still waiting to go out, keyed by dedupe key.

    A second upload has to ADD to the digest already queued for a day. Writing a
    fresh payload would silently discard everything the first upload put there,
    so the existing contents are read back and merged.
    """
    init()
    with engine.connect() as conn:
        rows = conn.execute(
            select(reminder_queue.c.dedupe_key, reminder_queue.c.payload).where(
                and_(
                    reminder_queue.c.destination == destination,
                    reminder_queue.c.channel == channel,
                    reminder_queue.c.status == "pending",
                    reminder_queue.c.dedupe_key.like(f"{channel}:{destination}:digest:%"),
                )
            )
        ).all()
    out: dict[str, dict] = {}
    for key, raw in rows:
        payload = _load(raw)
        if payload.get("digest"):
            out[key] = payload
    return out


def schedule_exam_reminders(
    to: str,
    exams: list[ExamEntry],
    reminder_minutes: list[int] | None = None,
    timezone: str = "UTC",
    channel: str = "email",
    user_id: str | None = None,
    schedule_id: str | None = None,
    mode: str = "smart",
) -> int:
    """Queue this student's reminders, then return how many rows are queued.

    Three layers, in order:
      1. reminder_policy picks each item's ladder from its kind.
      2. reminder_budget moves sends out of the small hours and decides which
         ones a student can actually absorb — the overflow is merged into one
         digest per day rather than dropped.
      3. this function writes the result to the queue.

    In "smart" mode — the default — the lead times come from the item's kind
    rather than from the caller, so an essay and an exam get different ladders
    without the student configuring anything. `reminder_minutes` is then only
    consulted in "custom" mode.

    Unlike the scheduler this replaced, this cannot silently return 0 because a
    background process isn't running — it only skips lead times that have
    genuinely already passed.
    """
    now = _now()

    # 1. Every reminder these items deserve, before considering volume.
    candidates: list[reminder_budget.Candidate] = []
    for exam in exams:
        due_at = parse_exam_datetime_in_timezone(exam.date, exam.time, timezone)
        if due_at is None:
            continue  # unusable date/time; the review table is where that's fixed
        for rung in reminder_policy.stages_for(exam, mode, reminder_minutes):
            send_at = due_at - timedelta(minutes=rung.minutes)
            # 2a. Nothing lands at 3am.
            send_at = reminder_budget.apply_quiet_hours(send_at, due_at, timezone)
            if send_at <= now:
                continue  # lead time already passed
            candidates.append(
                reminder_budget.Candidate(
                    item=exam, rung=rung, send_at=send_at, due_at=due_at
                )
            )

    if not candidates:
        return 0

    # 2b. Budget against what this student is ALREADY waiting on, not just this
    # batch — otherwise a second upload starts the count from zero and doubles
    # what reaches their inbox.
    # Rows THIS batch would write are not other people's noise — they are the
    # same reminders being re-planned. Counting them as already spent made a
    # second run of an unchanged schedule believe the week was full and open
    # extra digests, so running it twice produced more email than running it
    # once. Excluding them is what makes re-scheduling idempotent.
    existing_digests = pending_digests(to, channel)

    own_keys = {
        _individual_key(channel, to, c.item, c.rung.minutes) for c in candidates
    } | {
        _digest_key(channel, to, reminder_budget.local_day(c.send_at, timezone))
        for c in candidates
    }
    plan = reminder_budget.plan_sends(
        candidates,
        timezone,
        existing_send_times=pending_send_times(to, channel, exclude_keys=own_keys),
    )

    queued = 0

    for c in plan.individual:
        exam = c.item
        # The stage rides along in the payload so the sender can say what this
        # particular reminder is for, rather than restating the date.
        # stage says what this reminder is FOR and lead_minutes how far out it
        # is; together they are what lets the message name a concrete next
        # action instead of restating the date. See services/reminder_messages.
        payload = {
            **exam.model_dump(),
            "stage": c.rung.stage,
            "lead_minutes": c.rung.minutes,
            # Identity for the "I've done this" link; model_dump() cannot
            # include it because it is a method, not a field.
            "stable_key": exam.stable_key(),
            # Absolute deadline, so dispatch can tell a stale reminder from a
            # due one even after the queue sat unserved for days.
            "due_at": c.due_at.astimezone(dt_timezone.utc).isoformat(),
        }
        key = _individual_key(channel, to, exam, c.rung.minutes)
        if enqueue(
            destination=to,
            payload=payload,
            send_at=c.send_at,
            dedupe_key=key,
            channel=channel,
            user_id=user_id,
            schedule_id=schedule_id,
        ):
            queued += 1

    # One row per oversubscribed day, carrying everything that did not earn an
    # individual send. Keyed by the day alone so a later upload merges into the
    # same digest instead of creating a second one.
    for day, members in plan.digests.items():
        key = _digest_key(channel, to, day)
        # Merge with whatever this day's digest already holds, so a later upload
        # adds lines instead of replacing the ones already there.
        entries: list[dict] = list(existing_digests.get(key, {}).get("items") or [])
        seen = {_item_identity(e) for e in entries}
        for m in members:
            entry = {
                **m.item.model_dump(),
                "stage": m.rung.stage,
                "lead_minutes": m.rung.minutes,
                "stable_key": m.item.stable_key(),
                "due_at": m.due_at.astimezone(dt_timezone.utc).isoformat(),
            }
            identity = _item_identity(entry)
            if identity not in seen:
                seen.add(identity)
                entries.append(entry)
        payload = {"digest": True, "date": day.isoformat(), "items": entries}
        if enqueue(
            destination=to,
            payload=payload,
            send_at=reminder_budget.digest_send_time(day, timezone, members),
            dedupe_key=key,
            channel=channel,
            user_id=user_id,
            schedule_id=schedule_id,
            update_payload=True,
        ):
            queued += 1

    return queued


def pending_send_times(
    destination: str,
    channel: str = "email",
    exclude_keys: set[str] | None = None,
) -> list[datetime]:
    """When this student's not-yet-sent reminders are due to go out.

    The budget is per person, not per upload, so it has to see the rows already
    waiting. Only pending rows count: a reminder already sent has spent its
    attention, and a cancelled one never will.

    `exclude_keys` leaves out rows the caller is about to rewrite, so re-running
    a schedule does not treat its own previous output as competing traffic.
    """
    init()
    conditions = [
        reminder_queue.c.destination == destination,
        reminder_queue.c.channel == channel,
        reminder_queue.c.status == "pending",
    ]
    if exclude_keys:
        conditions.append(reminder_queue.c.dedupe_key.notin_(exclude_keys))
    with engine.connect() as conn:
        rows = conn.execute(
            select(reminder_queue.c.send_at).where(and_(*conditions))
        ).all()
    # SQLite hands back naive datetimes; the budget compares them across
    # timezones, so they are normalised to UTC-aware here.
    return [
        r[0] if r[0].tzinfo else r[0].replace(tzinfo=dt_timezone.utc) for r in rows
    ]


def reclaim_stalled() -> int:
    """Return rows stuck in 'sending' (process died mid-send) to the queue."""
    init()
    cutoff = _now() - STALLED_AFTER
    with engine.begin() as conn:
        result = conn.execute(
            update(reminder_queue)
            .where(
                and_(
                    reminder_queue.c.status == "sending",
                    reminder_queue.c.claimed_at < cutoff,
                )
            )
            .values(status="pending", claimed_at=None)
        )
    return result.rowcount or 0


def claim_due(limit: int = BATCH_SIZE) -> list[dict]:
    """Atomically take up to `limit` due reminders and mark them 'sending'.

    On Postgres the claim uses FOR UPDATE SKIP LOCKED, so two overlapping
    dispatch runs take disjoint sets and a reminder can never be sent twice.
    """
    init()
    now = _now()

    with engine.begin() as conn:
        if IS_POSTGRES:
            rows = conn.execute(
                sql_text(
                    """
                    update xamio.reminder_queue q
                       set status = 'sending',
                           attempts = q.attempts + 1,
                           claimed_at = :now
                     where q.id in (
                           select id from xamio.reminder_queue
                            where status = 'pending' and send_at <= :now
                            order by send_at
                            limit :limit
                              for update skip locked
                     )
                 returning q.id, q.channel, q.destination, q.payload,
                           q.attempts, q.max_attempts, q.send_at
                    """
                ),
                {"now": now, "limit": limit},
            ).mappings().all()
        else:
            # SQLite (local dev only) has no SKIP LOCKED.
            ids = [
                r[0]
                for r in conn.execute(
                    select(reminder_queue.c.id)
                    .where(
                        and_(
                            reminder_queue.c.status == "pending",
                            reminder_queue.c.send_at <= now,
                        )
                    )
                    .order_by(reminder_queue.c.send_at)
                    .limit(limit)
                ).all()
            ]
            if not ids:
                return []
            # Claim row by row, guarded on status: two overlapping runs can both
            # SELECT the same ids before either writes, and only the one whose
            # UPDATE still finds the row pending may send it.
            owned = []
            for rid in ids:
                result = conn.execute(
                    update(reminder_queue)
                    .where(and_(reminder_queue.c.id == rid, reminder_queue.c.status == "pending"))
                    .values(
                        status="sending",
                        attempts=reminder_queue.c.attempts + 1,
                        claimed_at=now,
                    )
                )
                if result.rowcount:
                    owned.append(rid)
            ids = owned
            if not ids:
                return []
            rows = conn.execute(
                select(
                    reminder_queue.c.id,
                    reminder_queue.c.channel,
                    reminder_queue.c.destination,
                    reminder_queue.c.payload,
                    reminder_queue.c.attempts,
                    reminder_queue.c.max_attempts,
                    reminder_queue.c.send_at,
                ).where(reminder_queue.c.id.in_(ids))
            ).mappings().all()

    return [{**dict(row), "payload": _load(row["payload"])} for row in rows]


def mark_sent(reminder_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(reminder_queue)
            .where(reminder_queue.c.id == reminder_id)
            .values(status="sent", sent_at=_now(), last_error=None)
        )


def mark_failed(reminder_id: int, error: str, attempts: int, max_attempts: int) -> None:
    """Put a failed send back on the queue, or give up once it has burned
    through its attempts — so one permanently bad address can't be retried
    forever."""
    exhausted = attempts >= max_attempts
    values = {
        "status": "failed" if exhausted else "pending",
        "last_error": error[:2000],
        "claimed_at": None,
    }
    if not exhausted:
        # Exponential backoff (2, 4, 8, 16 min…) so a provider outage is
        # ridden out over time instead of burning every attempt in five
        # consecutive one-minute ticks.
        values["send_at"] = _now() + timedelta(minutes=min(2 ** attempts, 60))
    with engine.begin() as conn:
        conn.execute(
            update(reminder_queue).where(reminder_queue.c.id == reminder_id).values(**values)
        )


def mark_cancelled(reminder_id: int, reason: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(reminder_queue)
            .where(reminder_queue.c.id == reminder_id)
            .values(status="cancelled", last_error=reason[:2000], claimed_at=None)
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)


def entry_due_at(entry: dict) -> datetime | None:
    """When the item itself happens, in UTC — or, for rows queued before the
    deadline was recorded, the latest moment it could be in any timezone."""
    raw = entry.get("due_at")
    if raw:
        try:
            return _aware(datetime.fromisoformat(raw))
        except ValueError:
            pass
    try:
        naive = datetime.fromisoformat(f"{entry.get('date')}T{entry.get('time') or '23:59'}")
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=dt_timezone.utc) + _MAX_TZ_OFFSET


def triage(item: dict, now: datetime | None = None) -> tuple[str, dict | str]:
    """Decide what dispatch should do with a claimed row.

    Returns ("send", payload) or ("cancel", reason). Exists because the queue
    can sit unserved — the API host was suspended for days once — and a
    backlog must not reach students as "your exam starts in 2 hours" about
    exams that are already over.
    """
    now = now or _now()
    payload = item["payload"]

    if payload.get("digest"):
        live = [e for e in payload.get("items") or [] if (entry_due_at(e) or now) > now]
        if not live:
            return "cancel", "expired: every item in this digest has passed"
        return "send", {**payload, "items": live}

    due = entry_due_at(payload)
    if due is not None and due <= now:
        return "cancel", "expired: the item has already happened"

    lead = payload.get("lead_minutes")
    send_at = item.get("send_at")
    if lead and send_at is not None:
        lateness = now - _aware(send_at)
        if lateness > max(LATE_FLOOR, timedelta(minutes=lead * LATE_FRACTION)):
            return "cancel", f"superseded: {lateness} late for a {lead}-minute reminder"
    return "send", payload


def cancel_for_schedule(schedule_id: str) -> int:
    """Drop the pending reminders for a schedule. Used when a re-uploaded
    timetable moves or removes exams, so nobody is reminded about a sitting
    that no longer exists."""
    init()
    with engine.begin() as conn:
        result = conn.execute(
            update(reminder_queue)
            .where(
                and_(
                    reminder_queue.c.schedule_id == schedule_id,
                    reminder_queue.c.status == "pending",
                )
            )
            .values(status="cancelled")
        )
    return result.rowcount or 0


def stats() -> dict:
    """Queue health — what the old scheduler could never tell you."""
    init()
    with engine.connect() as conn:
        rows = conn.execute(
            select(reminder_queue.c.status, func.count().label("n")).group_by(
                reminder_queue.c.status
            )
        ).all()
    return {status: count for status, count in rows}


def cancel_item(
    *,
    destination: str,
    stable_key: str | None,
    course_code: str | None,
    date: str | None,
    time_str: str | None,
    kind: str = "exam",
    channel: str = "email",
) -> int:
    """Stop the remaining reminders for ONE item the student says is done.

    Two places to clean up, because an item may appear in either or both:

      1. Its own pending rows. Their dedupe keys are deterministic and start
         with a fixed prefix per item, so a prefix match finds them without
         needing to query inside the JSON payload (which is Text on SQLite and
         jsonb on Postgres — one query would not work on both).
      2. Lines inside a day's digest. Those rows cover several items, so the
         row survives with that one line removed; it is only cancelled if the
         item was the last thing in it.

    Returns how many reminders stopped, counting digest lines individually —
    that is what the student would otherwise have received.
    """
    init()
    stopped = 0

    # Exam keys predate stable keys being used for identity, so they still use
    # the original course/date/time prefix. See _individual_key.
    if kind == "exam":
        if not (course_code and date and time_str):
            return 0
        prefix = f"{channel}:{destination}:{course_code}:{date}:{time_str}:"
    else:
        if not (stable_key and time_str):
            return 0
        prefix = f"{channel}:{destination}:{stable_key}:{time_str}:"

    with engine.begin() as conn:
        result = conn.execute(
            update(reminder_queue)
            .where(
                and_(
                    reminder_queue.c.destination == destination,
                    reminder_queue.c.channel == channel,
                    reminder_queue.c.status == "pending",
                    reminder_queue.c.dedupe_key.like(f"{prefix}%"),
                )
            )
            .values(status="cancelled")
        )
        stopped += result.rowcount or 0

    # Now the digests. Each is rewritten without this item.
    for key, payload in pending_digests(destination, channel).items():
        entries = payload.get("items") or []
        kept = []
        removed = 0
        for entry in entries:
            same = bool(stable_key) and entry.get("stable_key") == stable_key
            if same:
                removed += 1
            else:
                kept.append(entry)
        if not removed:
            continue
        stopped += removed
        with engine.begin() as conn:
            if kept:
                conn.execute(
                    update(reminder_queue)
                    .where(reminder_queue.c.dedupe_key == key)
                    .values(payload=_dump({**payload, "items": kept}))
                )
            else:
                # Nothing left to tell them about, so the email should not go.
                # The payload is emptied too, so a row inspected later reflects
                # what it would have sent rather than the last item removed.
                conn.execute(
                    update(reminder_queue)
                    .where(reminder_queue.c.dedupe_key == key)
                    .values(status="cancelled", payload=_dump({**payload, "items": []}))
                )

    return stopped
