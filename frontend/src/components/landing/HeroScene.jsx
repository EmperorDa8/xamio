import { useEffect, useRef, useState } from "react";
import { TIMETABLE, HERO_PICKS } from "./demoData";
import { daysFromNow, fmtDay, fmtTime, countdown, usePrefersReducedMotion, useTilt } from "./hooks";

// sheet → scan → lift → synced. Each phase is a class on the world; the CSS
// does the choreography so this component only has to keep time.
const PHASES = ["idle", "scan", "lift", "synced"];
const TIMING = [500, 1900, 1500]; // ms spent in idle, scan, lift before advancing

const ROW_H = 30;
const SHEET_TOP = 58; // header height inside the sheet

export default function HeroScene() {
  const reduced = usePrefersReducedMotion();
  const [phase, setPhase] = useState(reduced ? 3 : 0);
  const [run, setRun] = useState(0);
  const tiltRef = useTilt(7);
  const timers = useRef([]);

  useEffect(() => {
    if (reduced) {
      setPhase(3);
      return;
    }
    setPhase(0);
    let t = 0;
    timers.current = TIMING.map((ms, i) => {
      t += ms;
      return setTimeout(() => setPhase(i + 1), t);
    });
    return () => timers.current.forEach(clearTimeout);
  }, [run, reduced]);

  const picks = TIMETABLE
    .map((row, rowIndex) => ({ ...row, rowIndex }))
    .filter((r) => HERO_PICKS.includes(r.code));
  const next = daysFromNow(picks[0].day, picks[0].hour);
  const examDays = new Set(picks.map((p) => daysFromNow(p.day).getDate()));

  return (
    <div className={`hs hs-${PHASES[phase]}`} ref={tiltRef}>
      <div className="hs-stage" aria-hidden="true">
        <div className="hs-world">
          {/* the messy source document */}
          <div className="hs-sheet">
            <div className="hs-sheet-head">
              <b>Faculty of Science</b>
              <span>2nd semester examination timetable · v3 FINAL (2)</span>
            </div>
            {TIMETABLE.map((r, i) => {
              const hit = HERO_PICKS.includes(r.code);
              return (
                <div
                  key={r.code}
                  className={`hs-row${hit ? " is-hit" : ""}`}
                  style={{ "--i": i }}
                >
                  <span>{r.code}</span>
                  <span className="hs-row-t">{r.title}</span>
                  <span>{daysFromNow(r.day).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
                  <span>{r.venue}</span>
                </div>
              );
            })}
            <div className="hs-beam" />
          </div>

          {/* exams lifted off the sheet into cards */}
          {picks.map((p, i) => {
            const d = daysFromNow(p.day, p.hour);
            return (
              <div
                key={p.code}
                className={`hs-card tone-${p.tone}`}
                style={{
                  "--i": i,
                  "--from-y": `${SHEET_TOP + 40 + p.rowIndex * ROW_H}px`,
                  "--to-y": `${14 + i * 88}px`,
                }}
              >
                <div className="hs-card-top">
                  <span className="hs-code">{p.code}</span>
                  <span className="hs-chip">{countdown(d)}</span>
                </div>
                <b>{p.title}</b>
                <span className="hs-card-meta">
                  {fmtDay(d)} · {fmtTime(d)} · {p.venue}
                </span>
              </div>
            );
          })}

          {/* the calendar it lands in */}
          <div className="hs-cal">
            <div className="hs-cal-head">
              <span>Next 3 weeks</span>
              <span className="hs-cal-sync">● Synced</span>
            </div>
            <div className="hs-cal-grid">
              {Array.from({ length: 21 }, (_, i) => {
                const d = daysFromNow(i);
                return (
                  <span key={i} className={examDays.has(d.getDate()) ? "on" : i === 0 ? "today" : ""}>
                    {d.getDate()}
                  </span>
                );
              })}
            </div>
          </div>

          {/* and the reminder that actually saves you */}
          <div className="hs-toast">
            <span className="hs-toast-bell">🔔</span>
            <div>
              <b>{picks[0].code} {countdown(next)}</b>
              <span>{fmtTime(next)} · {picks[0].venue} · bring your ID</span>
            </div>
          </div>
        </div>
      </div>

      <div className="hs-caption">
        <span className="hs-steps">
          {["Read", "Match", "Sync"].map((s, i) => (
            <span key={s} className={phase > i ? "on" : ""}>{s}</span>
          ))}
        </span>
        <button type="button" className="hs-replay" onClick={() => setRun((n) => n + 1)}>
          ↻ Replay
        </button>
      </div>
    </div>
  );
}
