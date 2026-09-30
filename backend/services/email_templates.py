"""The HTML side of every email Xamio sends.

The plain-text bodies in reminder_messages stay the source of truth for what a
message SAYS; this module only decides how it looks. Each email goes out as
multipart/alternative, so a client that cannot render HTML still gets the full
text version rather than a stripped-down one.

Email HTML is its own dialect: tables for layout, every style inline, no
flexbox, no box-shadow (Gmail drops it), no custom properties. The site's
design system is translated into those limits rather than approximated:
  - cream canvas, ink type, coral as the single accent
  - each item is a pastel card sitting on a darker "ledge" of the same hue —
    here a thick bottom border, since shadows do not survive Gmail
  - Bricolage / Instrument Serif / JetBrains Mono load where the client
    allows web fonts (Apple Mail, iOS) and fall back to close system faces
    everywhere else.
"""
from __future__ import annotations

import os
from datetime import date as _date
from html import escape

from services import reminder_messages as rm

# ── tokens, mirrored from frontend/src/index.css ────────────────────────────
BG = "#f6f1e7"
CARD = "#fffdf8"
INK = "#15140f"
INK_2 = "#3a362d"
INK_3 = "#6f695b"
LINE = "#e4ddcd"
CORAL = "#ed6f5c"
CORAL_D = "#d9563f"

DISPLAY = "'Bricolage Grotesque','Helvetica Neue',Helvetica,Arial,sans-serif"
SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif"
BODY = "Inter,'Helvetica Neue',Helvetica,Arial,sans-serif"
MONO = "'JetBrains Mono',Menlo,Consolas,'Courier New',monospace"

FONTS_URL = (
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;"
    "12..96,800&family=Instrument+Serif:ital@0;1&family=Inter:wght@400;500;600"
    "&family=JetBrains+Mono:wght@500;600&display=swap"
)

# (pastel, ledge) per kind — the same pairs the landing page uses for items.
TONES = {
    "exam": ("#f8c9d1", "#e58a9b"),
    "test": ("#fbdcb4", "#e3a45a"),
    "coursework": ("#d9d1fa", "#8d7fe0"),
    "problem_set": ("#cfe4f8", "#5a9bd8"),
    "milestone": ("#c6ecd9", "#4fae83"),
    "meeting": ("#cfe4f8", "#5a9bd8"),
}

KIND_TAG = {
    "exam": "EXAM",
    "test": "TEST",
    "coursework": "COURSEWORK",
    "problem_set": "HOMEWORK",
    "milestone": "MILESTONE",
    "meeting": "MEETING",
}


def app_url() -> str:
    return (os.getenv("FRONTEND_URL") or "https://xamify-ten.vercel.app").rstrip("/")


# ── small helpers ───────────────────────────────────────────────────────────

def _e(value) -> str:
    return escape(str(value), quote=True)


def _pretty_date(raw: str | None) -> str:
    """'2026-10-01' -> 'Thu 1 Oct'. Anything unparseable is shown as given —
    the parser occasionally keeps a date it could not normalise, and showing it
    verbatim is better than hiding it."""
    if not raw:
        return ""
    try:
        d = _date.fromisoformat(raw)
    except ValueError:
        return raw
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _when(entry: dict) -> str:
    kind = entry.get("kind") or "exam"
    label, preposition = rm.KIND_LABEL.get(kind, rm.KIND_LABEL["exam"])
    day = _pretty_date(entry.get("date"))
    time = entry.get("time") or ""
    stamp = f"{day} · {time}" if day and time else day or time
    return f"{label} {preposition} {stamp}".strip()


def _tone(entry: dict) -> tuple[str, str]:
    return TONES.get(entry.get("kind") or "exam", TONES["exam"])


def _action_html(text: str) -> str:
    """Render an action prompt from reminder_messages.

    Indented, quoted lines in that copy are example slots — '"Wed 10:00-10:45,
    library - past paper Q1"'. They are drawn as a dashed, fill-in-the-blank
    chip, because the point is for the student to write their own.
    """
    out = []
    for para in text.strip().split("\n\n"):
        prose = []
        for line in para.split("\n"):
            line = line.strip()
            if line.startswith('"'):
                if prose:
                    out.append(_p(" ".join(prose)))
                    prose = []
                out.append(_slot(line.strip('"')))
            else:
                prose.append(line)
        if prose:
            out.append(_p(" ".join(prose)))
    return "".join(out)


def _p(text: str) -> str:
    return (
        f'<p style="margin:0 0 12px;font:500 16px/1.55 {BODY};color:{INK_2};">'
        # The shared copy uses ASCII hyphens so the text email stays plain;
        # in HTML a spaced hyphen should read as the dash it stands for.
        f"{_e(text).replace(' - ', ' &mdash; ')}</p>"
    )


def _slot(text: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:2px 0 14px;"><tr>'
        f'<td style="padding:10px 14px;border:1.5px dashed {INK_3};border-radius:12px;'
        f'background:{BG};font:500 13px/1.4 {MONO};color:{INK};">'
        f"{_e(text)}</td></tr></table>"
    )


def _button(href: str, label: str, *, dark: bool = False) -> str:
    bg, ledge = (INK, "#000000") if dark else (CORAL, CORAL_D)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="display:inline-table;margin:0 8px 10px 0;"><tr>'
        f'<td style="border-radius:999px;background:{bg};border-bottom:4px solid {ledge};">'
        f'<a href="{_e(href)}" target="_blank" style="display:inline-block;padding:13px 24px;'
        f'font:700 15px/1 {DISPLAY};letter-spacing:-0.01em;color:#ffffff;text-decoration:none;'
        f'border-radius:999px;">{_e(label)} &rarr;</a></td></tr></table>'
    )


def _eyebrow(text: str) -> str:
    return (
        f'<div style="font:600 11px/1 {MONO};letter-spacing:0.14em;text-transform:uppercase;'
        f'color:{CORAL_D};margin:0 0 14px;">&#9679;&nbsp; {_e(text)}</div>'
    )


def _headline(plain: str, accent: str | None = None) -> str:
    """Bricolage headline with an Instrument Serif italic accent and the coral
    full stop that closes the landing-page hero."""
    accent_html = (
        f' <em style="font-family:{SERIF};font-style:italic;font-weight:400;'
        f'letter-spacing:-0.01em;">{_e(accent)}</em>'
        if accent else ""
    )
    return (
        f'<h1 style="margin:0 0 14px;font:800 34px/1.08 {DISPLAY};letter-spacing:-0.035em;'
        f'color:{INK};">{_e(plain)}{accent_html}<span style="color:{CORAL};">.</span></h1>'
    )


def _item_card(entry: dict, *, compact: bool = False) -> str:
    tone, ledge = _tone(entry)
    kind = entry.get("kind") or "exam"
    code = entry.get("course_code") or "Deadline"
    name = entry.get("title") or entry.get("course_name") or ""

    details = []
    if entry.get("venue"):
        details.append(("Where", _e(entry["venue"])))
    if entry.get("weight_pct") is not None:
        details.append(("Worth", f"{entry['weight_pct']:g}% of final grade"))
    if entry.get("submission_url"):
        url = _e(entry["submission_url"])
        details.append(
            ("Submit", f'<a href="{url}" style="color:{INK};font-weight:600;">Submission link</a>')
        )
    detail_rows = "".join(
        f'<tr><td style="padding:3px 12px 0 0;font:600 11px/1.6 {MONO};letter-spacing:0.08em;'
        f'text-transform:uppercase;color:{INK_3};white-space:nowrap;vertical-align:top;">{k}</td>'
        f'<td style="padding:3px 0 0;font:500 14px/1.5 {BODY};color:{INK_2};">{v}</td></tr>'
        for k, v in details
    )
    title_size = 18 if compact else 22
    pad = "16px 18px" if compact else "20px 22px"

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:0 0 12px;"><tr>'
        f'<td style="background:{tone};border-radius:18px;border-bottom:5px solid {ledge};padding:{pad};">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font:600 12px/1 {MONO};letter-spacing:0.04em;color:{INK};">'
        f'<span style="display:inline-block;padding:6px 10px;background:{CARD};border-radius:999px;">'
        f"{_e(code)}</span></td>"
        f'<td align="right" style="font:600 10px/1 {MONO};letter-spacing:0.14em;color:{INK_2};">'
        f"{KIND_TAG.get(kind, 'DEADLINE')}</td></tr></table>"
        + (
            f'<div style="margin:12px 0 4px;font:700 {title_size}px/1.2 {DISPLAY};'
            f'letter-spacing:-0.02em;color:{INK};">{_e(name)}</div>'
            if name else '<div style="height:10px;line-height:10px;">&nbsp;</div>'
        )
        + f'<div style="font:600 14px/1.5 {BODY};color:{INK};">{_e(_when(entry))}</div>'
        + (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin-top:8px;">{detail_rows}</table>'
            if detail_rows else ""
        )
        + "</td></tr></table>"
    )


def _next_move(entry: dict, heading: str = "Your next move") -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:8px 0 22px;"><tr>'
        f'<td style="background:{CARD};border:1px solid {LINE};border-left:4px solid {CORAL};'
        'border-radius:18px;padding:20px 22px 8px;">'
        f'<div style="font:600 11px/1 {MONO};letter-spacing:0.14em;text-transform:uppercase;'
        f'color:{INK_3};margin:0 0 12px;">{_e(heading)}</div>'
        f"{_action_html(rm.action_for(entry))}"
        "</td></tr></table>"
    )


def _layout(*, preheader: str, tag: str, content: str) -> str:
    """Page shell: canvas, brand header, white sheet, footer."""
    home = app_url()
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>Xamio</title>
<link href="{FONTS_URL}" rel="stylesheet">
<style>
  body {{ margin:0; padding:0; background:{BG}; }}
  a {{ color:{INK}; }}
  @media (max-width:600px) {{
    .x-sheet {{ padding:28px 20px !important; }}
    .x-wrap {{ padding:16px 10px 28px !important; }}
    h1 {{ font-size:28px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{BG};">{_e(preheader)}&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BG};">
<tr><td align="center" class="x-wrap" style="padding:28px 16px 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:580px;">
    <!-- brand -->
    <tr><td style="padding:0 6px 18px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td>
          <a href="{_e(home)}" target="_blank" style="text-decoration:none;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
              <td width="34" height="34" align="center" valign="middle" style="width:34px;height:34px;background:{INK};border-radius:10px;border-right:3px solid {CORAL};border-bottom:3px solid {CORAL};font:italic 400 21px/34px {SERIF};color:{BG};">X</td>
              <td style="padding-left:10px;font:800 21px/1 {DISPLAY};letter-spacing:-0.03em;color:{INK};">Xamio</td>
            </tr></table>
          </a>
        </td>
        <td align="right">
          <span style="display:inline-block;padding:7px 12px;border:1px solid {LINE};background:{CARD};border-radius:999px;font:600 10px/1 {MONO};letter-spacing:0.14em;color:{INK_2};">{_e(tag)}</span>
        </td>
      </tr></table>
    </td></tr>
    <!-- sheet -->
    <tr><td class="x-sheet" style="background:{CARD};border:1px solid {LINE};border-radius:28px;padding:36px 34px;">
      {content}
    </td></tr>
    <!-- footer -->
    <tr><td style="padding:22px 12px 0;text-align:center;font:500 12px/1.6 {BODY};color:{INK_3};">
      You're getting this because you turned on reminders in Xamio.<br>
      <a href="{_e(home)}" target="_blank" style="color:{INK_2};font-weight:600;">Open Xamio</a>
      &nbsp;&middot;&nbsp; made for students who'd rather not cram<span style="color:{CORAL};">.</span>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


# ── the three emails ────────────────────────────────────────────────────────

def reminder_html(entry: dict, to: str | None = None) -> str:
    kind = entry.get("kind") or "exam"
    label, _ = rm.KIND_LABEL.get(kind, rm.KIND_LABEL["exam"])
    noun = "deadline" if kind in ("coursework", "problem_set") else label.lower()
    raw_lead = entry.get("lead_minutes")
    lead = rm.humanize_lead(raw_lead) if raw_lead else None

    headline = _headline(f"Your {noun} is", lead or "coming up")
    buttons = ""
    if entry.get("submission_url"):
        buttons += _button(entry["submission_url"], "Open submission page")
    done = rm.done_link(entry, to)
    if done:
        verb = "I've sat it" if kind in ("exam", "test") else "I've handed it in"
        buttons += _button(done, verb, dark=bool(buttons))

    content = (
        _eyebrow(f"Reminder · {lead}" if lead else "Reminder")
        + headline
        + f'<p style="margin:0 0 24px;font:500 16px/1.55 {BODY};color:{INK_3};">'
        "Not a nag — a nudge with a plan attached.</p>"
        + _item_card(entry)
        + _next_move(entry)
        + (
            f'<div style="padding-top:4px;">{buttons}</div>'
            f'<p style="margin:6px 0 0;font:500 12px/1.5 {BODY};color:{INK_3};">'
            "Done already? One tap stops the rest of these reminders.</p>"
            if done else (f"<div>{buttons}</div>" if buttons else "")
        )
    )
    code = entry.get("course_code") or "Deadline"
    return _layout(
        preheader=f"{code} {lead or 'coming up'} — here's your next move.",
        tag=KIND_TAG.get(kind, "REMINDER"),
        content=content,
    )


def digest_html(payload: dict, to: str | None = None) -> str:
    items = sorted(
        payload.get("items") or [],
        key=lambda e: (e.get("date") or "", e.get("time") or ""),
    )
    count = len(items)
    things = "thing" if count == 1 else "things"

    cards = "".join(_item_card(e, compact=True) for e in items)
    first = items[0] if items else None
    start = ""
    if first:
        start = _next_move(first, heading=f"Start with {rm._heading(first)}")

    done_links = ""
    if 0 < count <= rm.MAX_DIGEST_DONE_LINKS:
        links = [
            (e, rm.done_link(e, to)) for e in items
        ]
        links = [(e, l) for e, l in links if l]
        if links:
            done_links = (
                f'<div style="margin:18px 0 0;padding-top:18px;border-top:1px solid {LINE};'
                f'font:500 13px/1.8 {BODY};color:{INK_3};">Already done? '
                + " &middot; ".join(
                    f'<a href="{_e(l)}" style="color:{INK};font-weight:600;">'
                    f"{_e(e.get('course_code') or 'this one')}</a>"
                    for e, l in links
                )
                + "</div>"
            )

    content = (
        _eyebrow("Daily digest")
        + _headline(f"{count} {things}", "on deck")
        + f'<p style="margin:0 0 24px;font:500 16px/1.55 {BODY};color:{INK_3};">'
        "Bundled into one email so your inbox stays readable.</p>"
        + start
        + cards
        + done_links
    )
    return _layout(
        preheader=f"{count} {things} coming up — start with "
        + (rm._heading(first) if first else "the first one"),
        tag="DIGEST",
        content=content,
    )


def summary_html(entries: list[dict], noun: str) -> str:
    count = len(entries)
    plural = noun if count == 1 else f"{noun}s"
    cards = "".join(_item_card(e, compact=True) for e in entries)

    content = (
        _eyebrow(f"{count} {plural} · synced")
        + _headline("Your schedule is", "locked in")
        + f'<p style="margin:0 0 24px;font:500 16px/1.55 {BODY};color:{INK_3};">'
        f"Everything we pulled from your upload, in date order. We'll check in "
        f"before each {noun} with a concrete next step — not just the date.</p>"
        + cards
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:14px 0 22px;"><tr>'
        f'<td style="background:{BG};border-radius:18px;padding:16px 18px;'
        f'font:500 14px/1.55 {BODY};color:{INK_2};">'
        f'<span style="font:600 11px/1 {MONO};letter-spacing:0.12em;color:{INK};">'
        "&#128206;&nbsp; EXAMS.ICS ATTACHED</span><br>"
        "Open it to add every date to Google Calendar, Apple Calendar or Outlook in one go."
        "</td></tr></table>"
        + _button(app_url(), "Open Xamio")
    )
    return _layout(
        preheader=f"{count} {plural} added — reminders are set.",
        tag="SCHEDULE",
        content=content,
    )
