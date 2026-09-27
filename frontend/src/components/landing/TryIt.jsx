import { useMemo, useState } from "react";
import { TIMETABLE } from "./demoData";
import { daysFromNow, fmtDay, fmtTime, countdown } from "./hooks";
import { IconArrow, IconBell } from "../icons";

const LEADS = [
  { label: "1 day", mins: 1440 },
  { label: "3 hours", mins: 180 },
  { label: "30 min", mins: 30 },
];

function leadText(mins) {
  if (mins >= 1440) return "tomorrow";
  if (mins >= 60) return `in ${mins / 60} hours`;
  return `in ${mins} minutes`;
}

/**
 * The hook: a hands-on version of what Xamio does. Visitors pick "their"
 * courses and watch a whole faculty timetable collapse to just their exams,
 * with a live preview of the reminder they'd get.
 */
export default function TryIt({ onStart }) {
  const [mine, setMine] = useState(["CSC 201", "MTH 211", "CSC 209"]);
  const [lead, setLead] = useState(180);

  const toggle = (code) =>
    setMine((m) => (m.includes(code) ? m.filter((c) => c !== code) : [...m, code]));

  const picked = useMemo(
    () =>
      TIMETABLE.filter((r) => mine.includes(r.code)).map((r) => ({
        ...r,
        date: daysFromNow(r.day, r.hour),
      })),
    [mine],
  );
  const first = picked[0];

  return (
    <div className="ti">
      <div className="ti-left">
        <p className="ti-kicker">1 · Tap the courses you're registered for</p>
        <div className="ti-chips" role="group" aria-label="Your courses">
          {TIMETABLE.map((r) => {
            const on = mine.includes(r.code);
            return (
              <button
                key={r.code}
                type="button"
                className={`ti-chip tone-${r.tone}${on ? " on" : ""}`}
                aria-pressed={on}
                onClick={() => toggle(r.code)}
              >
                <span className="ti-chip-tick">{on ? "✓" : "+"}</span>
                {r.code}
              </button>
            );
          })}
        </div>

        <p className="ti-kicker">2 · Xamio reads the full timetable</p>
        <div className="ti-sheet" aria-live="polite">
          {TIMETABLE.map((r) => {
            const on = mine.includes(r.code);
            return (
              <div key={r.code} className={`ti-row${on ? " on" : ""}`}>
                <span className="ti-code">{r.code}</span>
                <span className="ti-title">{r.title}</span>
                <span className="ti-when">{fmtDay(daysFromNow(r.day))}</span>
              </div>
            );
          })}
        </div>
        <p className="ti-count">
          <b>{TIMETABLE.length}</b> exams on the sheet → <b className="coral">{picked.length}</b> that are actually yours.
        </p>
      </div>

      <div className="ti-right">
        <p className="ti-kicker">3 · You get only your exams, counting down</p>
        <div className="ti-stack">
          {picked.length === 0 && (
            <div className="ti-empty">Pick a course on the left — your exams stack up here.</div>
          )}
          {picked.map((p, i) => (
            <article
              key={p.code}
              className={`ti-card tone-${p.tone}`}
              style={{ "--i": i }}
            >
              <div className="ti-card-top">
                <span className="ti-card-code">{p.code}</span>
                <span className="ti-card-count">{countdown(p.date)}</span>
              </div>
              <h4>{p.title}</h4>
              <p>{fmtDay(p.date)} · {fmtTime(p.date)} · {p.venue}</p>
            </article>
          ))}
        </div>

        <p className="ti-kicker">4 · Choose when to be reminded</p>
        <div className="ti-leads" role="radiogroup" aria-label="Reminder timing">
          {LEADS.map((l) => (
            <button
              key={l.mins}
              type="button"
              role="radio"
              aria-checked={lead === l.mins}
              className={`ti-lead${lead === l.mins ? " on" : ""}`}
              onClick={() => setLead(l.mins)}
            >
              {l.label} before
            </button>
          ))}
        </div>

        <div className={`ti-phone${first ? "" : " is-off"}`}>
          <div className="ti-notif" key={`${first?.code}-${lead}`}>
            <span className="ti-notif-ico"><IconBell /></span>
            <div>
              <span className="ti-notif-app">Xamio · now</span>
              <b>{first ? `${first.code} ${first.title} ${leadText(lead)}` : "No exams picked yet"}</b>
              <span>{first ? `${fmtTime(first.date)} · ${first.venue}. You've got this.` : "Tap a course to see your reminder."}</span>
            </div>
          </div>
        </div>

        <button type="button" className="x-btn x-btn-primary ti-cta" onClick={() => onStart("signup")}>
          Do this with my real timetable
          <span className="x-btn-arrow"><IconArrow /></span>
        </button>
      </div>
    </div>
  );
}
