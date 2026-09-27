import html
import logging
import os
import secrets
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
import anyio
from fastapi.responses import Response, RedirectResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from models import (
    ParsedTimetable,
    SyncRequest,
    SyncResult,
    GoogleAuthResponse,
    EmailAlertRequest,
    EmailAlertResult,
)
from services.parser import parse_timetable
from services.ical_service import build_ics
from services.google_calendar import get_auth_url_and_state, exchange_code, sync_exams_to_google
from services.email_service import (
    check_email_config,
    send_digest_email,
    send_reminder_email,
    send_summary_email,
)
from services import reminder_queue
from services import done_tokens
from services import token_store
from services import tickets
from services.auth import require_user

# Reject uploads larger than this to protect memory and AI token spend.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024

limiter = Limiter(key_func=get_remote_address)

# Parsing is CPU-bound pure Python (pdfplumber) plus slow AI calls. Letting
# every upload run at once starves the rest of the API of the interpreter; a
# small cap keeps /health, .ics and email responsive while uploads queue.
PARSE_SLOTS = anyio.CapacityLimiter(int(os.getenv("PARSE_CONCURRENCY", "2")))

app = FastAPI(title="Exam Timer API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def _startup():
    # Say up front if email cannot actually reach students. This one fails
    # silently otherwise: everything looks healthy, reminders queue and get
    # marked sent, and only the account owner ever receives one.
    email_problem = check_email_config()
    if email_problem:
        logging.getLogger("xamio").warning("Email: %s", email_problem)

    # Warm the queue table. Failure is logged, never fatal: a bad DATABASE_URL
    # must not stop the service binding a port, or /health and /parse go down
    # with it even though neither touches Postgres. The tables are created
    # lazily on first use anyway (services/db.prepare).
    try:
        reminder_queue.init()
    except Exception as e:
        logging.getLogger("xamio").error(
            "Database unavailable at startup — reminders and Google Calendar "
            "sync will fail until DATABASE_URL is correct. Error: %s",
            e,
        )

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# In production the frontend (xamio.app) calls this API cross-site
# (onrender.com), and browsers only attach cross-site XHR cookies when they are
# SameSite=None; Secure. Local dev over http://localhost stays Lax (same-site).
_SECURE_COOKIES = FRONTEND_URL.startswith("https")

# Allow the configured production frontend plus localhost. The production site
# is served from the xamio.app custom domain; Vercel also issues a new preview
# domain per deploy (e.g. xamify-<hash>-<team>.vercel.app). We allow the custom
# domain (apex + any subdomain) and this project's Vercel subdomains via a regex
# — overridable with FRONTEND_URL_REGEX. This stops CORS from breaking on every
# redeploy or domain change.
_allowed_origins = list(
    {FRONTEND_URL, "https://xamio.app", "https://www.xamio.app", "http://localhost:5173"}
)
_origin_regex = os.getenv(
    "FRONTEND_URL_REGEX",
    r"https://([\w-]+\.)?xamio\.app|https://xamify[\w-]*\.vercel\.app",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


def _validate_exam_schedule(exams) -> list[str]:
    """Check a schedule before it reaches a calendar or an inbox.

    Hard-fails (422) only on what we genuinely cannot turn into an event: a
    missing or unparseable date/time. The review table lets the user fix those.

    Sitting two exams on one day is normal at large multi-faculty universities
    (a morning paper and an afternoon paper), so it is NOT an error — blocking
    it stopped those students exporting at all. Only a genuine *time overlap*
    is reported, and as a returned warning rather than a block: an overlap is
    usually a parse slip worth a second look, but it is the university's
    timetable, and refusing to export it helps nobody.

    Overlap only means anything for things you attend. A deadline occupies no
    time — five assignments due at 23:59 on the same Friday is a hard week, not
    a conflict, and warning about it would make the warnings worthless.
    """
    from services.datetime_utils import parse_exam_datetime

    problems: list[str] = []
    warnings: list[str] = []
    # date -> (course code, start, end) for the sittings we could place in time
    by_date: dict[str, list[tuple[str, datetime, datetime]]] = {}

    for exam in exams:
        start = parse_exam_datetime(exam.date, exam.time)
        if start is None:
            problems.append(
                f"{exam.display_name()}: invalid or missing date/time "
                f"(date={exam.date!r}, time={exam.time!r})."
            )
            continue
        if not exam.is_attended:
            continue
        end = start + timedelta(minutes=exam.duration_minutes or 120)
        by_date.setdefault(exam.date, []).append((exam.course_code, start, end))

    if problems:
        raise HTTPException(
            status_code=422,
            detail="Schedule check failed: " + " | ".join(problems),
        )

    for date, sittings in by_date.items():
        if len(sittings) < 2:
            continue
        sittings.sort(key=lambda s: s[1])
        for (code_a, _, end_a), (code_b, start_b, _) in zip(sittings, sittings[1:]):
            if start_b < end_a:
                warnings.append(
                    f"{code_a} and {code_b} overlap on {date} "
                    f"({code_a} runs until {end_a:%H:%M}, {code_b} starts at {start_b:%H:%M}). "
                    "Check the times in the review table if that looks wrong."
                )

    return warnings


def _validate_timezone(name: str) -> None:
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(name)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Unknown timezone: {name!r}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/parse", response_model=ParsedTimetable)
@limiter.limit("5/minute")
async def parse(
    request: Request,
    file: UploadFile = File(...),
    courses_file: UploadFile | None = File(None),
    courses_text: str = Form(""),
    user: dict = Depends(require_user),
):
    allowed = {"pdf", "png", "jpg", "jpeg", "webp", "tiff", "bmp", "xlsx", "xls", "csv", "docx", "txt"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext}")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    courses_bytes = None
    if courses_file and courses_file.filename:
        courses_ext = courses_file.filename.rsplit(".", 1)[-1].lower() if "." in courses_file.filename else ""
        if courses_ext not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported course-list file type: .{courses_ext}")
        courses_bytes = await courses_file.read()
        if len(courses_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Course list file too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            )

    # PDF extraction and the AI calls block for seconds (up to 2x45s on a
    # slow provider). Run them in the threadpool: on the event loop, one long
    # upload froze the whole API — health checks included — for every user.
    try:
        result = await anyio.to_thread.run_sync(
            lambda: parse_timetable(
                file.filename,
                file_bytes,
                registered_courses_text=courses_text,
                registered_courses_filename=courses_file.filename if courses_file else None,
                registered_courses_bytes=courses_bytes,
            ),
            limiter=PARSE_SLOTS,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logging.getLogger("xamio").exception("parse failed for %s", file.filename)
        raise HTTPException(
            status_code=422,
            detail="We couldn't read that file. Try a PDF, spreadsheet or Word export of the timetable.",
        )

    return result


@app.post("/download/ics")
def download_ics(body: SyncRequest, user: dict = Depends(require_user)):
    _validate_exam_schedule(body.exams)
    _validate_timezone(body.timezone)
    ics_bytes = build_ics(
        body.exams, body.reminder_minutes, body.timezone, mode=body.reminder_mode
    )
    return Response(
        content=ics_bytes,
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=exams.ics"},
    )


@app.post("/alerts/email", response_model=EmailAlertResult)
@limiter.limit("5/minute")
def email_alerts(
    request: Request, body: EmailAlertRequest, user: dict = Depends(require_user)
):
    if not body.exams:
        raise HTTPException(status_code=400, detail="No exams to send.")
    # With auth enforced, only send to the signed-in user's own address — this
    # endpoint must not double as a spam relay for arbitrary recipients.
    token_email = (user.get("email") or "").strip().lower()
    if token_email and body.email.strip().lower() != token_email:
        raise HTTPException(
            status_code=403,
            detail=f"Alerts can only be sent to your sign-in email ({token_email}).",
        )
    warnings = _validate_exam_schedule(body.exams)
    try:
        send_summary_email(
            body.email,
            body.exams,
            body.reminder_minutes,
            body.timezone,
            mode=body.reminder_mode,
        )
        scheduled = reminder_queue.schedule_exam_reminders(
            body.email,
            body.exams,
            body.reminder_minutes,
            body.timezone,
            user_id=user.get("sub"),
            mode=body.reminder_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not send email: {e}")

    if scheduled:
        message = (
            f"Summary sent to {body.email}. "
            f"{scheduled} reminder email(s) queued before your exams."
        )
    else:
        # Nothing queued now means every lead time has already passed — the only
        # remaining reason, since queueing no longer depends on a live scheduler.
        message = (
            f"Summary sent to {body.email} with your full schedule attached. "
            "No timed reminders were queued because every reminder time you "
            "chose has already passed."
        )

    return EmailAlertResult(
        success=True, message=message, scheduled=scheduled, warnings=warnings
    )


def _require_task_key(provided: str | None) -> None:
    """Guard for machine-triggered endpoints.

    Deliberately fails CLOSED, unlike the Supabase user auth: an unconfigured
    TASK_SECRET disables dispatch rather than leaving an endpoint that sends
    mail to anyone who finds it.
    """
    secret = os.getenv("TASK_SECRET")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Reminder dispatch is not configured. Set TASK_SECRET.",
        )
    if not provided or not secrets.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="Invalid task key.")


@app.post("/tasks/dispatch-reminders")
def dispatch_reminders(x_task_key: str | None = Header(default=None)):
    """Send every reminder that has come due. Called on a schedule by pg_cron
    via pg_net; safe to call by hand or to run twice at once.

    Rows are claimed atomically before sending, so overlapping runs take
    disjoint work and no reminder goes out twice. A send that fails goes back on
    the queue until it runs out of attempts, so a transient email outage delays
    delivery instead of losing it.
    """
    _require_task_key(x_task_key)

    reclaimed = reminder_queue.reclaim_stalled()
    due = reminder_queue.claim_due()

    sent = 0
    failed = 0
    skipped = 0
    for item in due:
        action, detail = reminder_queue.triage(item)
        if action == "cancel":
            reminder_queue.mark_cancelled(item["id"], detail)
            skipped += 1
            continue
        payload = detail
        try:
            if item["channel"] == "email":
                # A digest row stands in for several reminders that landed on
                # one day; see services/reminder_budget.
                if payload.get("digest"):
                    send_digest_email(item["destination"], payload)
                else:
                    send_reminder_email(item["destination"], payload)
            else:
                # whatsapp/sms are queued but have no sender wired up yet.
                raise RuntimeError(f"No sender for channel {item['channel']!r}")
            reminder_queue.mark_sent(item["id"])
            sent += 1
        except Exception as e:
            reminder_queue.mark_failed(
                item["id"], str(e), item["attempts"], item["max_attempts"]
            )
            failed += 1

    return {
        "claimed": len(due),
        "sent": sent,
        "failed": failed,
        "skipped_stale": skipped,
        "reclaimed": reclaimed,
        "queue": reminder_queue.stats(),
    }


@app.get("/tasks/reminder-stats")
def reminder_stats(x_task_key: str | None = Header(default=None)):
    """Queue health at a glance — the old scheduler could not answer
    "did the reminders actually go out?" at all."""
    _require_task_key(x_task_key)
    return reminder_queue.stats()


def _done_page(title: str, message: str, token: str | None = None) -> Response:
    """Small self-contained page. No JS, no external assets — it is opened from
    an email client's browser, often on a phone, sometimes offline-ish."""
    action = ""
    if token:
        action = (
            '<form method="post" action="/reminders/done">'
            f'<input type="hidden" name="token" value="{html.escape(token)}">'
            '<button type="submit">Yes, stop reminding me</button>'
            "</form>"
        )
    return Response(
        content=(
            "<!doctype html><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            "<title>Xamio</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;"
            "padding:0 1.5rem;color:#15140f;background:#faf8f3;line-height:1.5}"
            "h1{font-size:1.25rem;margin:0 0 .5rem}p{color:#555}"
            "button{margin-top:1.5rem;padding:.7rem 1.4rem;font-size:1rem;border:0;"
            "border-radius:8px;background:#d65a3c;color:#fff;cursor:pointer}</style>"
            f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{action}"
        ),
        media_type="text/html",
    )


@app.get("/reminders/done")
def done_confirm(token: str = ""):
    """Landing page for the "done" link in a reminder email.

    Deliberately does NOT cancel anything. Mail clients and security scanners
    fetch the links in a message before anyone opens it, so a GET that changed
    state would silence reminders the student never touched. The cancel happens
    on the POST below, which a scanner will not perform.
    """
    try:
        body = done_tokens.parse_token(token)
    except done_tokens.TokenError as e:
        return _done_page("That link didn't work", str(e))

    label = body.get("c") or "this"
    return _done_page(
        "Mark this as done?",
        f"We'll stop the remaining reminders for {label}. "
        "Your calendar entries stay where they are.",
        token=token,
    )


@app.post("/reminders/done")
@limiter.limit("20/minute")
def done_submit(request: Request, token: str = Form(default="")):
    try:
        body = done_tokens.parse_token(token)
    except done_tokens.TokenError as e:
        return _done_page("That link didn't work", str(e))

    stopped = reminder_queue.cancel_item(
        destination=body["to"],
        stable_key=body.get("sk"),
        course_code=body.get("c"),
        date=body.get("d"),
        time_str=body.get("t"),
        kind=body.get("k") or "exam",
    )
    if stopped:
        return _done_page(
            "Done — nicely handled.",
            f"{stopped} reminder{'s' if stopped != 1 else ''} stopped. "
            "You'll still hear about everything else.",
        )
    return _done_page(
        "Already taken care of",
        "There were no reminders left to stop for this one.",
    )


@app.get("/auth/google", response_model=GoogleAuthResponse)
def google_auth(request: Request, user: dict = Depends(require_user)):
    if not os.getenv("GOOGLE_CLIENT_ID"):
        raise HTTPException(status_code=501, detail="Google Calendar not configured. Add GOOGLE_CLIENT_ID to .env")
    # Hand back our own /start URL: the browser navigates there top-level, so
    # the CSRF state cookie is set first-party on this origin before Google.
    base = str(request.base_url).rstrip("/")
    # A top-level navigation can't carry the Bearer token, so hand the browser a
    # short-lived signed ticket naming the user. The callback uses it to store
    # the Google tokens against that user — not against a cookie, which Safari
    # and Firefox drop on the later cross-site sync/status requests.
    ticket = tickets.issue({"sub": user.get("sub")}, ttl_seconds=600)
    return {"auth_url": f"{base}/auth/google/start?ticket={ticket}"}


@app.get("/auth/google/start")
def google_auth_start(ticket: str = ""):
    """Top-level navigation target: stamp the CSRF state cookie (and the user
    ticket), then redirect to Google's consent screen."""
    if not os.getenv("GOOGLE_CLIENT_ID"):
        raise HTTPException(status_code=501, detail="Google Calendar not configured. Add GOOGLE_CLIENT_ID to .env")
    auth_url, state = get_auth_url_and_state()
    response = RedirectResponse(auth_url)
    cookie = dict(httponly=True, samesite="lax", secure=_SECURE_COOKIES, max_age=600)
    # Lax: both are sent on the top-level redirect back from Google.
    response.set_cookie("oauth_state", state, **cookie)
    if ticket:
        response.set_cookie("oauth_ticket", ticket, **cookie)
    return response


@app.get("/auth/google/callback")
async def google_callback(code: str, request: Request, state: str | None = None):
    # CSRF check: the state Google echoes back must match the cookie stamped by
    # /auth/google/start in this same browser — otherwise an attacker could
    # bind THEIR Google account to the victim's session.
    expected_state = request.cookies.get("oauth_state")
    if not expected_state or not state or not secrets.compare_digest(expected_state, state):
        raise HTTPException(
            status_code=400,
            detail="OAuth state mismatch — restart the Google Calendar connection from the app.",
        )
    try:
        creds = exchange_code(code)
        sub = None
        try:
            sub = tickets.verify(request.cookies.get("oauth_ticket") or "").get("sub")
        except tickets.TicketError:
            pass
        # Persist credentials keyed to the session cookie so they survive restarts.
        session_key = request.cookies.get("exam_sync_session") or secrets.token_urlsafe(24)
        token_store.save_credentials(_user_key(sub) if sub else session_key, creds)
        response = RedirectResponse(f"{FRONTEND_URL}?google_connected=true")
        response.set_cookie(
            "exam_sync_session",
            session_key,
            httponly=True,
            # None+Secure so later cross-site XHRs (sync/status) include it.
            samesite="none" if _SECURE_COOKIES else "lax",
            secure=_SECURE_COOKIES,
            max_age=60 * 60 * 24 * 7,
        )
        response.delete_cookie("oauth_state")
        response.delete_cookie("oauth_ticket")
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/sync/google", response_model=SyncResult)
def sync_google(
    body: SyncRequest, request: Request, user: dict = Depends(require_user)
):
    creds = _google_credentials(user, request)
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated with Google. Connect Google Calendar first.")

    warnings = _validate_exam_schedule(body.exams)
    _validate_timezone(body.timezone)
    try:
        event_ids, removed = sync_exams_to_google(
            creds,
            body.exams,
            body.reminder_minutes,
            body.timezone,
            stale_keys=body.stale_keys,
            mode=body.reminder_mode,
        )
        message = f"Synced {len(event_ids)} exam event(s) to Google Calendar."
        if removed:
            message += f" Removed {removed} event(s) for exams that moved or were dropped."
        return SyncResult(
            success=True,
            message=message,
            event_ids=event_ids,
            warnings=warnings,
        )
    except Exception as e:
        logging.getLogger("xamio").exception("Google sync failed")
        text = str(e)
        if "invalid_grant" in text or "Token has been expired or revoked" in text:
            raise HTTPException(
                status_code=401,
                detail="Your Google connection has expired. Connect Google Calendar again.",
            )
        raise HTTPException(status_code=502, detail="Google Calendar didn't accept the sync. Try again in a minute.")


def _user_key(sub: str) -> str:
    return f"user:{sub}"


def _google_credentials(user: dict, request: Request) -> dict | None:
    """Google tokens for this user. Falls back to the legacy per-browser cookie
    and, when found, re-files them under the user so the cookie stops mattering."""
    sub = user.get("sub")
    if sub:
        creds = token_store.get_credentials(_user_key(sub))
        if creds:
            return creds
    legacy = token_store.get_credentials(request.cookies.get("exam_sync_session"))
    if legacy and sub:
        token_store.save_credentials(_user_key(sub), legacy)
    return legacy


@app.get("/auth/google/status")
def google_status(request: Request, user: dict = Depends(require_user)):
    return {"connected": _google_credentials(user, request) is not None}
