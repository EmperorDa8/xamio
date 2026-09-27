import { useEffect, useRef, useState } from "react";
import { usePrefersReducedMotion } from "./hooks";

/**
 * "What's new" showcase: one tab per recent capability, each with a small
 * live demo. Every claim here maps to shipped behaviour — photo parsing
 * (vision model), item kinds, one-tap "done" links, quiet hours + daily cap
 * with a digest (services/reminder_budget), and re-upload change detection.
 */

const TABS = [
  { id: "photo", tone: "pink", label: "Snap a photo", title: "Just take a photo of the notice board", body: "No PDF? Point your phone at the printed timetable. Xamio reads the photo and pulls out your exams — then you check every date before anything reaches your calendar." },
  { id: "deadlines", tone: "peach", label: "Every deadline", title: "Exams, and every deadline around them", body: "Upload a syllabus or assignment brief too. Essays, weekly homework, thesis milestones and supervisor meetings each get recognised — and reminded — for what they are." },
  { id: "done", tone: "mint", label: "One-tap done", title: "Handed it in? One tap stops the reminders", body: "Every reminder email has an “I've done this” link. Tap it and the rest of that item's reminders stop — no app, no sign-in, nothing else changes." },
  { id: "quiet", tone: "lavender", label: "Quiet inbox", title: "Never a 3am email. Never a flood.", body: "Nothing lands between 10pm and 7am. At most two reminder emails a day — on a heavy day the rest are gathered into one 6pm digest, so nothing is dropped." },
  { id: "changes", tone: "sky", label: "Change alerts", title: "Exam moved? You'll see exactly what changed", body: "Re-upload the revised timetable and Xamio shows what moved, what was added and what was dropped — then clears the old date out of your calendar." },
];

function PhotoDemo({ play }) {
  return (
    <div className={`wn-photo${play ? " play" : ""}`}>
      <div className="wn-phone">
        <div className="wn-notice">
          {Array.from({ length: 7 }, (_, i) => <span key={i} className={i === 2 || i === 5 ? "hit" : ""} />)}
          <i className="wn-flash" />
        </div>
        <span className="wn-shutter" />
      </div>
      <div className="wn-out">
        <div className="ti-card tone-pink"><div className="ti-card-top"><span className="ti-card-code">CSC 201</span><span className="ti-card-count">Thu 9:00</span></div><h4>Data Structures</h4></div>
        <div className="ti-card tone-lavender"><div className="ti-card-top"><span className="ti-card-code">CSC 209</span><span className="ti-card-count">Mon 13:00</span></div><h4>Database Systems</h4></div>
      </div>
    </div>
  );
}

const KINDS = [
  { tag: "EXAM", tone: "pink", t: "CSC 201 · Data Structures", w: "Thu 9:00 · Hall B" },
  { tag: "DUE", tone: "mint", t: "ENG 203 · Essay 2", w: "Fri 23:59" },
  { tag: "HOMEWORK", tone: "peach", t: "MTH 211 · Problem set 6", w: "Wed evening" },
  { tag: "MILESTONE", tone: "lavender", t: "Thesis · Chapter 2 draft", w: "in 3 weeks" },
  { tag: "MEETING", tone: "sky", t: "Supervisor meeting", w: "Tue 14:00" },
];

function DeadlinesDemo({ play }) {
  return (
    <ul className={`wn-kinds${play ? " play" : ""}`}>
      {KINDS.map((k, i) => (
        <li key={k.tag} className={`tone-${k.tone}`} style={{ "--i": i }}>
          <b>{k.tag}</b>
          <span>{k.t}</span>
          <small>{k.w}</small>
        </li>
      ))}
    </ul>
  );
}

function DoneDemo() {
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(false), 4000);
    return () => clearTimeout(t);
  }, [done]);
  return (
    <div className="wn-done">
      <div className="wn-mail">
        <div className="wn-mail-head"><b>Xamio</b><span>ENG 203 Essay 2 is due tomorrow</span></div>
        <p>Finish and submit tonight if you possibly can. Leave tomorrow as slack for the upload going wrong.</p>
        <button type="button" className={`wn-done-btn${done ? " on" : ""}`} onClick={() => setDone(true)}>
          {done ? "✓ Done — reminders stopped" : "I've done this"}
        </button>
      </div>
      <ul className={`wn-ladder${done ? " stopped" : ""}`} aria-label="Remaining reminders">
        {["3 hours before", "Due · 23:59"].map((r) => <li key={r}>{r}</li>)}
      </ul>
      {!done && <p className="wn-hint">↑ Try it</p>}
    </div>
  );
}

function QuietDemo({ play }) {
  const hours = [
    { h: "03:00", moved: true, label: "Exam reminder" },
    { h: "07:00", label: "Exam reminder", arrive: true },
    { h: "12:00", label: "Essay due in 3 days" },
    { h: "18:00", label: "Digest · 4 more items", digest: true },
  ];
  return (
    <div className={`wn-quiet${play ? " play" : ""}`}>
      <div className="wn-night"><span>Quiet 22:00 – 07:00</span></div>
      {hours.map((x, i) => (
        <div key={x.h} className={`wn-slot${x.moved ? " moved" : ""}${x.arrive ? " arrive" : ""}${x.digest ? " digest" : ""}`} style={{ "--i": i }}>
          <time>{x.h}</time>
          <span>{x.label}</span>
        </div>
      ))}
      <p className="wn-cap">Max 2 reminder emails a day · everything else, one digest</p>
    </div>
  );
}

function ChangesDemo({ play }) {
  return (
    <div className={`wn-changes${play ? " play" : ""}`}>
      <div className="wn-change moved"><b>Moved</b><span>MTH 211</span><s>Tue 7 Oct</s><em>→ Mon 13 Oct</em></div>
      <div className="wn-change added"><b>Added</b><span>STA 202</span><em>Wed 15 Oct · 13:00</em></div>
      <div className="wn-change removed"><b>Removed</b><span>GST 101</span><s>Fri 10 Oct</s></div>
      <p className="wn-cap">Old dates are removed from your calendar on the next sync.</p>
    </div>
  );
}

const DEMOS = { photo: PhotoDemo, deadlines: DeadlinesDemo, done: DoneDemo, quiet: QuietDemo, changes: ChangesDemo };

export default function WhatsNew() {
  const [active, setActive] = useState(0);
  const [auto, setAuto] = useState(true);
  const [play, setPlay] = useState(false);
  const reduced = usePrefersReducedMotion();
  const rootRef = useRef(null);

  // Only start cycling once the section is on screen.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !("IntersectionObserver" in window)) return setPlay(true);
    const io = new IntersectionObserver(([e]) => setPlay(e.isIntersecting), { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!auto || !play || reduced) return;
    const t = setTimeout(() => setActive((a) => (a + 1) % TABS.length), 6500);
    return () => clearTimeout(t);
  }, [active, auto, play, reduced]);

  const tab = TABS[active];
  const Demo = DEMOS[tab.id];

  return (
    <div className="wn" ref={rootRef}>
      <div className="wn-tabs" role="tablist" aria-label="New in Xamio">
        {TABS.map((t, i) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`wn-tab-${t.id}`}
            aria-selected={i === active}
            aria-controls="wn-panel"
            className={`wn-tab tone-${t.tone}${i === active ? " on" : ""}`}
            onClick={() => { setActive(i); setAuto(false); }}
          >
            <span className="wn-new">New</span>
            {t.label}
            {i === active && auto && play && !reduced && <i className="wn-progress" key={active} />}
          </button>
        ))}
      </div>

      <div className={`wn-panel tone-${tab.tone}`} role="tabpanel" id="wn-panel" aria-labelledby={`wn-tab-${tab.id}`}>
        <div className="wn-copy" key={`c-${tab.id}`}>
          <h3>{tab.title}</h3>
          <p>{tab.body}</p>
        </div>
        <div className="wn-stage" key={`d-${tab.id}`}>
          <Demo play={play} />
        </div>
      </div>
    </div>
  );
}
