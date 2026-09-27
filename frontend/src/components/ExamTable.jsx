import { useState } from "react";

// Must stay in step with ItemKind in backend/models.py. The kind decides which
// reminder schedule a row gets, so it is worth letting the student correct a
// misread here rather than silently reminding them about an essay as if it
// were an exam.
export const KINDS = [
  { value: "exam", label: "Exam" },
  { value: "test", label: "Test" },
  { value: "coursework", label: "Assignment" },
  { value: "problem_set", label: "Homework" },
  { value: "milestone", label: "Milestone" },
  { value: "meeting", label: "Meeting" },
];

// Things you turn up to, as opposed to things you submit — mirrors
// ATTENDED_KINDS on the backend. Venue and duration only apply to these.
const ATTENDED = new Set(["exam", "test", "meeting"]);

function Cell({ value, onChange, type = "text", className = "", disabled = false, placeholder }) {
  return (
    <input
      type={type}
      value={value ?? ""}
      disabled={disabled}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className={`cell-input ${className}`}
    />
  );
}

function KindCell({ value, onChange }) {
  return (
    <select
      value={value || "exam"}
      onChange={(e) => onChange(e.target.value)}
      className={`cell-input kind kind-${value || "exam"}`}
    >
      {KINDS.map((k) => (
        <option key={k.value} value={k.value}>{k.label}</option>
      ))}
    </select>
  );
}

export default function ExamTable({ exams, onChange }) {
  const [selected, setSelected] = useState(new Set(exams.map((_, i) => i)));

  const emit = (updated, sel) => onChange(updated, sel);

  const toggleAll = () => {
    const next = selected.size === exams.length ? new Set() : new Set(exams.map((_, i) => i));
    setSelected(next);
    emit(exams, next);
  };

  const toggle = (i) => {
    const next = new Set(selected);
    next.has(i) ? next.delete(i) : next.add(i);
    setSelected(next);
    emit(exams, next);
  };

  const updateExam = (index, field, value) => {
    const updated = exams.map((e, i) => {
      if (i !== index) return e;
      const next = { ...e, [field]: value };
      // Switching between something you attend and something you submit changes
      // which fields are even meaningful. Clearing them here keeps the row
      // consistent with what the backend will accept, and stops a leftover
      // venue showing up on a calendar entry for an essay.
      if (field === "kind") {
        if (ATTENDED.has(value)) {
          next.duration_minutes = e.duration_minutes || 120;
        } else {
          next.duration_minutes = null;
          next.venue = null;
          // End of day is the near-universal convention for a hand-in, and a
          // 09:00 carried over from an exam row would be wrong by 15 hours.
          if (!e.time || e.time === "09:00") next.time = "23:59";
        }
      }
      return next;
    });
    emit(updated, selected);
  };

  const selectedCount = [...selected].filter((i) => i < exams.length).length;

  return (
    <div>
      <div className="table-head">
        <h2>Detected deadlines<span className="count">{selectedCount}/{exams.length}</span></h2>
        <span className="coord">click any cell to edit</span>
      </div>

      <div className="table-wrap">
        <table className="exams">
          <thead>
            <tr>
              <th style={{ width: 44 }}>
                <input type="checkbox" className="cb"
                  checked={selected.size === exams.length && exams.length > 0}
                  onChange={toggleAll} />
              </th>
              <th>Type</th>
              <th>Code</th>
              <th>Course</th>
              <th>Item</th>
              <th>Date</th>
              <th>Time</th>
              <th>Min</th>
              <th>Venue</th>
            </tr>
          </thead>
          <tbody>
            {exams.map((exam, i) => (
              <tr key={i} className={selected.has(i) ? "" : "off"}>
                <td>
                  <input type="checkbox" className="cb"
                    checked={selected.has(i)} onChange={() => toggle(i)} />
                </td>
                <td><KindCell value={exam.kind} onChange={(v) => updateExam(i, "kind", v)} /></td>
                <td><Cell className="code" value={exam.course_code} onChange={(v) => updateExam(i, "course_code", v)} /></td>
                <td><Cell value={exam.course_name} onChange={(v) => updateExam(i, "course_name", v)} /></td>
                <td><Cell value={exam.title} placeholder={ATTENDED.has(exam.kind || "exam") ? "—" : "e.g. Essay 2"}
                  onChange={(v) => updateExam(i, "title", v)} /></td>
                <td>
                  <div className="date-cell">
                    <Cell type="date" value={exam.date}
                      className={exam.date_verified === false ? "unverified" : ""}
                      onChange={(v) => updateExam(i, "date", v)} />
                    {exam.date_verified === false && (
                      <span className="date-flag" title={exam.date_note || "Date could not be confirmed against the timetable — please check."}>⚠</span>
                    )}
                  </div>
                </td>
                <td><Cell type="time" value={exam.time} onChange={(v) => updateExam(i, "time", v)} /></td>
                {/* Duration and venue describe a room you sit in. A submission
                    has neither, so they are disabled rather than hidden — the
                    columns stay aligned across a mixed schedule. */}
                <td><Cell type="number" value={exam.duration_minutes}
                  disabled={!ATTENDED.has(exam.kind || "exam")}
                  onChange={(v) => updateExam(i, "duration_minutes", parseInt(v) || 120)} /></td>
                <td><Cell value={exam.venue}
                  disabled={!ATTENDED.has(exam.kind || "exam")}
                  onChange={(v) => updateExam(i, "venue", v)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="table-note">Uncheck rows to exclude them from your calendar.</p>
    </div>
  );
}
