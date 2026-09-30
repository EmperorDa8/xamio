import base64
import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from models import ExamEntry
from services.ical_service import build_ics
from services import reminder_messages
from services import email_templates

RESEND_ENDPOINT = "https://api.resend.com/emails"

# Resend's shared sender. It works without any DNS setup, but it can ONLY
# deliver to the account owner's own address — so a deployment left on it looks
# healthy while silently refusing every real student.
RESEND_SHARED_SENDER = "Xamio <onboarding@resend.dev>"


def resend_sender() -> str:
    return os.getenv("RESEND_FROM") or RESEND_SHARED_SENDER


def check_email_config() -> str | None:
    """Describe a misconfiguration that would stop reminders reaching students.

    Verifying a sending domain in Resend does nothing on its own: delivery only
    leaves the owner-only shared sender once RESEND_FROM points at that domain.
    That combination — real API key, forgotten RESEND_FROM — fails silently for
    everyone except the account owner, who is usually the person testing it. It
    is worth saying out loud at startup.
    """
    if os.getenv("RESEND_API_KEY"):
        if not os.getenv("RESEND_FROM"):
            return (
                "RESEND_API_KEY is set but RESEND_FROM is not, so email is going out "
                f"as {RESEND_SHARED_SENDER}. Resend only delivers from that shared "
                "sender to your OWN account address — reminders to students will be "
                "rejected. Verify a domain at https://resend.com/domains and set "
                "RESEND_FROM, e.g. 'Xamio <alerts@xamio.app>'."
            )
        return None

    if os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
        return (
            "No RESEND_API_KEY — falling back to SMTP. That works locally, but "
            "Render blocks outbound SMTP, so reminders will NOT send in production."
        )

    return (
        "No email transport configured (set RESEND_API_KEY, or SMTP_USER + "
        "SMTP_PASSWORD). Reminders will be queued but never delivered."
    )


def _send_via_resend(
    to: str, subject: str, text: str, ics_bytes: bytes | None, html: str | None = None
) -> None:
    """Send over Resend's HTTPS API. Works on hosts (e.g. Render) that block
    outbound SMTP ports, since this rides on port 443."""
    api_key = os.getenv("RESEND_API_KEY")
    sender = resend_sender()
    payload = {"from": sender, "to": [to], "subject": subject, "text": text}
    if html:
        payload["html"] = html
    if ics_bytes:
        payload["attachments"] = [
            {"filename": "exams.ics", "content": base64.b64encode(ics_bytes).decode()}
        ]
    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend is fronted by Cloudflare, which blocks the default
            # "Python-urllib" agent (403 code 1010). Send a normal UA.
            "User-Agent": "Xamio/1.0 (+https://xamify-ten.vercel.app)",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        # Resend rejects any recipient other than the account owner until a
        # sending domain is verified. Surface a clear, user-friendly message
        # (raised as ValueError -> 501) instead of a raw API dump.
        if e.code == 403 and ("verify a domain" in detail or "your own email address" in detail):
            raise ValueError(
                "Email alerts aren't available for this address yet. For now, use "
                "“Download .ics” or “Connect Google Calendar” to add your exams — "
                "email reminders for everyone are coming soon."
            )
        raise RuntimeError(f"Resend API error {e.code}: {detail}")


def _send_via_smtp(
    to: str, subject: str, text: str, ics_bytes: bytes | None, html: str | None = None
) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    # Google shows an app password grouped as "abcd efgh ijkl mnop", so that is
    # what people paste. SMTP AUTH rejects it with the spaces in, failing as
    # "BadCredentials" — which reads like a wrong password rather than a
    # formatting problem, and sends you looking in the wrong place.
    password = (os.getenv("SMTP_PASSWORD") or "").replace(" ", "")
    sender = os.getenv("SMTP_FROM", user or "")
    if not user or not password:
        raise ValueError(
            "Email is not configured. Set RESEND_API_KEY (recommended for hosting), "
            "or SMTP_USER + SMTP_PASSWORD for local SMTP."
        )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(text)
    if html:
        # Text first, HTML second: clients show the last alternative they can
        # render, so HTML wins where supported and text is the fallback.
        msg.add_alternative(html, subtype="html")
    if ics_bytes:
        msg.add_attachment(ics_bytes, maintype="text", subtype="calendar", filename="exams.ics")
    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)


def _send(
    to: str,
    subject: str,
    text: str,
    ics_bytes: bytes | None = None,
    html: str | None = None,
) -> None:
    """Prefer the Resend HTTP API when configured (works on Render and other
    hosts that block SMTP); otherwise fall back to direct SMTP for local dev."""
    if os.getenv("RESEND_API_KEY"):
        _send_via_resend(to, subject, text, ics_bytes, html)
    else:
        _send_via_smtp(to, subject, text, ics_bytes, html)


def _format_exam(exam: ExamEntry) -> str:
    label, preposition = reminder_messages.KIND_LABEL[exam.kind]
    # display_name already folds in the course name when there is no item title,
    # so appending it again would read "CIT216 — Programming (Programming)".
    line = exam.display_name()
    if exam.title and exam.course_name:
        line += f" ({exam.course_name})"
    when = f"{label} {preposition} {exam.date} at {exam.time}"
    venue = f" — {exam.venue}" if exam.venue else ""
    return f"  • {line}\n      {when}{venue}"


def send_summary_email(
    to: str,
    exams: list[ExamEntry],
    reminder_minutes: list[int] | None = None,
    timezone: str = "UTC",
    mode: str = "smart",
) -> None:
    """One immediate email listing every exam, with the .ics attached."""
    ordered = sorted(exams, key=lambda e: (e.date or "", e.time or ""))
    lines = [_format_exam(e) for e in ordered]
    # "exams" only reads correctly when that is all there is; a syllabus upload
    # is mostly deadlines.
    noun = "exam" if all(e.kind == "exam" for e in exams) else "deadline"
    plural = f"{noun}s" if len(exams) != 1 else noun
    body = (
        f"Here is your schedule ({len(exams)} {plural}):\n\n"
        + "\n".join(lines)
        + "\n\nThe attached exams.ics can be imported into any calendar app.\n"
        + f"You will also receive reminder emails before each {noun}.\n\n"
        + "— Xamio"
    )
    ics = build_ics(exams, reminder_minutes, timezone, mode=mode)
    html = email_templates.summary_html([e.model_dump() for e in ordered], noun)
    _send(to, f"Your schedule — {len(exams)} {plural}", body, ics_bytes=ics, html=html)


def send_digest_email(to: str, payload: dict) -> None:
    """One email standing in for several reminders that fell on the same day.

    This is what keeps a heavy week from becoming twenty separate emails. It is
    a batching decision, not a dropping one: everything that lost its individual
    slot is still here, just in one place.
    """
    subject, body = reminder_messages.build_digest(payload, to)
    _send(to, subject, body, html=email_templates.digest_html(payload, to))


def send_reminder_email(to: str, exam: dict) -> None:
    """A single reminder, sent by the dispatcher ahead of a deadline. `exam` is
    a plain dict — it round-trips through the queue row's JSON payload.

    The wording comes from services/reminder_messages, which turns the stage and
    lead time into a concrete next action rather than restating the date.

    Rows queued before stages existed carry neither field; the message builder
    falls back to a generic action line, so those still read sensibly.
    """
    subject, body = reminder_messages.build_reminder(exam, to)
    _send(to, subject, body, html=email_templates.reminder_html(exam, to))
