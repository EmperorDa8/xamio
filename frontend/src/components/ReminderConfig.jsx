const PRESETS = [
  { label: "1 week", minutes: 10080 },
  { label: "3 days", minutes: 4320 },
  { label: "1 day", minutes: 1440 },
  { label: "12 hours", minutes: 720 },
  { label: "3 hours", minutes: 180 },
  { label: "1 hour", minutes: 60 },
  { label: "30 min", minutes: 30 },
];

// Plain-English summary of what services/reminder_policy.py will actually do.
// Kept deliberately short: this is here to build trust that something sensible
// happens by default, not to document the ladders.
const SMART_SUMMARY = [
  { kind: "Exams", detail: "2 weeks, 1 week, 3 days, the day before, then 2 hours before with the room" },
  { kind: "Assignments", detail: "a nudge to start, then 1 week, 2 days, the day before, and 3 hours before the deadline" },
  { kind: "Homework", detail: "one reminder the evening before — no more than that" },
  { kind: "Milestones", detail: "a month out, then 2 weeks, 1 week, and 2 days" },
  { kind: "Meetings", detail: "3 days ahead to prepare, the day before, and an hour before" },
];

export default function ReminderConfig({ reminders, onChange, mode = "smart", onModeChange }) {
  const toggle = (minutes) => {
    if (reminders.includes(minutes)) onChange(reminders.filter((m) => m !== minutes));
    else onChange([...reminders, minutes].sort((a, b) => b - a));
  };

  const smart = mode === "smart";

  return (
    <div>
      <div className="mode-switch" style={{ marginTop: 0 }}>
        <button
          className={`mode-opt${smart ? " on" : ""}`}
          onClick={() => onModeChange?.("smart")}
        >
          <span className="mode-title">Smart reminders</span>
          <span className="mode-sub">Picked from what each item is</span>
        </button>
        <button
          className={`mode-opt${!smart ? " on" : ""}`}
          onClick={() => onModeChange?.("custom")}
        >
          <span className="mode-title">Set my own</span>
          <span className="mode-sub">One schedule for everything</span>
        </button>
      </div>

      {smart ? (
        <div className="smart-summary">
          {SMART_SUMMARY.map(({ kind, detail }) => (
            <div key={kind} className="smart-row">
              <span className="smart-kind">{kind}</span>
              <span className="smart-detail">{detail}</span>
            </div>
          ))}
          <p className="coord" style={{ marginTop: 14 }}>
            an exam and an essay need different warning — so they get different warning
          </p>
        </div>
      ) : (
        <>
          <p className="coord" style={{ marginBottom: 18 }}>
            each alert fires this long before every item, whatever it is
          </p>
          <div className="presets">
            {PRESETS.map(({ label, minutes }) => {
              const on = reminders.includes(minutes);
              return (
                <button key={minutes} onClick={() => toggle(minutes)} className={`preset${on ? " on" : ""}`}>
                  {on && <span className="tick">✓</span>}
                  {label} before
                </button>
              );
            })}
          </div>
          {reminders.length === 0 && (
            <p className="alert error" style={{ marginTop: 16 }}>
              <span className="glyph">!</span> Pick at least one reminder time.
            </p>
          )}
        </>
      )}
    </div>
  );
}
