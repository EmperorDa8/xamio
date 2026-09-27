import { supabase } from "./supabase";
import { stableKey } from "./stableKey";

/**
 * Persistence for parsed exam schedules (public.schedules + public.schedule_exams).
 *
 * Until now a parsed timetable lived only in App's React state, so a refresh —
 * or simply coming back tomorrow — threw away the user's work and made them
 * re-upload and re-pay the AI parse. These helpers store the schedule against
 * the signed-in user and read the most recent one back on load.
 *
 * RLS enforces ownership, but we stamp user_id explicitly (same as analytics.js)
 * because the insert policy checks it.
 *
 * Unlike analytics, failures here are NOT swallowed silently: losing the user's
 * schedule is a product failure, not a telemetry blip. Callers decide how loud
 * to be — App keeps the in-memory copy either way, so a save failure degrades to
 * today's behaviour rather than breaking the flow.
 */

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Course codes are compared with punctuation and case stripped, so "CSC-101",
 *  "csc 101" and "CSC101" are understood to be the same course. Matches the
 *  normalisation in ExamEntry.stable_key(). */
function normCode(code) {
  return (code || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** Only a well-formed ISO date reaches a `date` column; anything the parser
 *  could not resolve is stored as null so the exam survives for the user to
 *  fix, instead of failing the whole save. */
function toDateColumn(value) {
  const v = (value || "").trim();
  return ISO_DATE.test(v) ? v : null;
}

/** Item fields added after the first release (migration
 *  add_item_kind_to_schedule_exams). Without them a restored assignment came
 *  back as an "exam" — wrong reminders, wrong calendar title, and a different
 *  calendar key, so the next sync duplicated it. */
const ITEM_FIELDS = ["kind", "title", "weight_pct", "est_effort_hours", "submission_url"];

const BASE_EXAM_COLUMNS =
  "course_code, course_name, exam_date, exam_time, duration_minutes, venue, date_verified, date_note";

/** Postgres/PostgREST "that column doesn't exist" — i.e. the migration hasn't
 *  been applied yet. The app keeps working on the old columns until it is. */
function isMissingColumn(error) {
  return error?.code === "42703" || error?.code === "PGRST204" || /column .* does not exist|Could not find the '.*' column/i.test(error?.message || "");
}

function toExamRows(exams, scheduleId, userId, { withItemFields = true } = {}) {
  return Promise.all(
    exams.map(async (exam, i) => ({
      schedule_id: scheduleId,
      user_id: userId,
      stable_key: await stableKey(exam),
      ...(withItemFields
        ? {
            kind: exam.kind || "exam",
            title: exam.title ?? null,
            weight_pct: exam.weight_pct ?? null,
            est_effort_hours: exam.est_effort_hours ?? null,
            submission_url: exam.submission_url ?? null,
          }
        : {}),
      course_code: exam.course_code,
      course_name: exam.course_name ?? null,
      exam_date: toDateColumn(exam.date),
      exam_time: exam.time ?? null,
      duration_minutes: exam.duration_minutes ?? null,
      venue: exam.venue ?? null,
      date_verified: exam.date_verified ?? null,
      date_note: exam.date_note ?? null,
      position: i,
    })),
  );
}

/** Shape stored rows back into the ExamEntry shape the app and API expect. */
function toExamEntries(rows) {
  return (rows || []).map((r) => ({
    course_code: r.course_code,
    course_name: r.course_name,
    date: r.exam_date || "",
    time: r.exam_time || "",
    duration_minutes: r.duration_minutes,
    venue: r.venue,
    date_verified: r.date_verified,
    date_note: r.date_note,
    kind: r.kind || "exam",
    title: r.title ?? null,
    weight_pct: r.weight_pct ?? null,
    est_effort_hours: r.est_effort_hours ?? null,
    submission_url: r.submission_url ?? null,
  }));
}

/** Upsert exam rows, falling back to the pre-migration columns if needed. */
async function writeExamRows(exams, scheduleId, userId) {
  const rows = await toExamRows(exams, scheduleId, userId);
  let { error } = await supabase
    .from("schedule_exams")
    .upsert(rows, { onConflict: "schedule_id,stable_key", ignoreDuplicates: true });
  if (error && isMissingColumn(error)) {
    const legacy = await toExamRows(exams, scheduleId, userId, { withItemFields: false });
    ({ error } = await supabase
      .from("schedule_exams")
      .upsert(legacy, { onConflict: "schedule_id,stable_key", ignoreDuplicates: true }));
  }
  if (error) throw error;
}

async function currentUser() {
  const {
    data: { user },
  } = await supabase.auth.getUser();
  return user || null;
}

/**
 * Store a freshly parsed timetable. Returns the new schedule id, or null when
 * there is no signed-in user (nothing to own the rows).
 *
 * Two exams that normalise to the same course+date collide on the table's
 * unique key; upsert-ignore keeps the first rather than failing the save.
 */
export async function saveSchedule({
  exams,
  registeredCourses = [],
  unmatchedCourses = [],
  dateWarnings = [],
  modelUsed = null,
  sourceFilename = null,
  timezone = null,
}) {
  const user = await currentUser();
  if (!user) return null;

  const { data: schedule, error: scheduleError } = await supabase
    .from("schedules")
    .insert({
      user_id: user.id,
      source_filename: sourceFilename,
      model_used: modelUsed,
      timezone:
        timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      registered_courses: registeredCourses,
      unmatched_courses: unmatchedCourses,
      date_warnings: dateWarnings,
    })
    .select("id")
    .single();

  if (scheduleError) throw scheduleError;

  if (exams?.length) {
    await writeExamRows(exams, schedule.id, user.id);
  }

  return schedule.id;
}

/**
 * Replace the exams on an existing schedule — used after the user edits rows in
 * the review table, so what we stored matches what they actually approved.
 */
export async function replaceScheduleExams(scheduleId, exams) {
  const user = await currentUser();
  if (!user || !scheduleId) return;

  const { error: deleteError } = await supabase
    .from("schedule_exams")
    .delete()
    .eq("schedule_id", scheduleId);
  if (deleteError) throw deleteError;

  if (!exams?.length) return;

  await writeExamRows(exams, scheduleId, user.id);
}

/**
 * Most recently uploaded schedule for the signed-in user, or null if they have
 * never parsed one. This is what lets the app restore state on load.
 */
export async function loadLatestSchedule() {
  const user = await currentUser();
  if (!user) return null;

  const { data: schedule, error } = await supabase
    .from("schedules")
    .select(
      "id, source_filename, model_used, timezone, registered_courses, unmatched_courses, date_warnings, created_at",
    )
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) throw error;
  if (!schedule) return null;

  const readRows = (columns) =>
    supabase
      .from("schedule_exams")
      .select(columns)
      .eq("schedule_id", schedule.id)
      .order("position", { ascending: true });

  let { data: rows, error: examsError } = await readRows(
    `${BASE_EXAM_COLUMNS}, ${ITEM_FIELDS.join(", ")}`,
  );
  if (examsError && isMissingColumn(examsError)) {
    ({ data: rows, error: examsError } = await readRows(BASE_EXAM_COLUMNS));
  }

  if (examsError) throw examsError;

  return {
    id: schedule.id,
    exams: toExamEntries(rows),
    registeredCourses: schedule.registered_courses || [],
    unmatchedCourses: schedule.unmatched_courses || [],
    dateWarnings: schedule.date_warnings || [],
    modelUsed: schedule.model_used,
    sourceFilename: schedule.source_filename,
    timezone: schedule.timezone,
    createdAt: schedule.created_at,
  };
}

/** What makes two rows "the same item" across uploads. Deliberately NOT the
 *  date — a moved exam must still pair up with its old self. */
function itemIdentity(exam) {
  const code = normCode(exam.course_code);
  if (!code) return "";
  const kind = exam.kind || "exam";
  if (kind === "exam") return code;
  return `${code}|${kind}|${(exam.title || "").trim().toLowerCase()}`;
}

/**
 * Compare a newly parsed timetable against the one already stored.
 *
 * Universities reschedule exams mid-semester and students usually find out by
 * word of mouth. Re-uploading the revised timetable should therefore tell the
 * user *what moved*, not silently replace everything.
 *
 * Exams are matched on course code, NOT on stable_key: the key deliberately
 * includes the date (so calendar events stay idempotent), which means a moved
 * exam would otherwise look like an unrelated delete plus add rather than a
 * move. Pure and synchronous so it is easy to test.
 */
export function diffSchedules(previousExams, nextExams) {
  const before = new Map();
  for (const exam of previousExams || []) {
    const key = itemIdentity(exam);
    // First occurrence wins: a duplicated code in the old schedule shouldn't
    // change which row we consider the "before" state.
    if (key && !before.has(key)) before.set(key, exam);
  }

  const matched = new Set();
  const added = [];
  const changed = [];
  const unchanged = [];

  for (const exam of nextExams || []) {
    const key = itemIdentity(exam);
    const previous = key ? before.get(key) : null;
    if (!previous) {
      added.push(exam);
      continue;
    }
    matched.add(key);

    const fields = [
      ["date", "Date"],
      ["time", "Time"],
      ["venue", "Venue"],
    ].filter(([f]) => (previous[f] || "") !== (exam[f] || ""));

    if (fields.length) {
      changed.push({
        before: previous,
        after: exam,
        changes: fields.map(([field, label]) => ({
          field,
          label,
          from: previous[field] || "—",
          to: exam[field] || "—",
        })),
      });
    } else {
      unchanged.push(exam);
    }
  }

  const removed = [...before.entries()]
    .filter(([key]) => !matched.has(key))
    .map(([, exam]) => exam);

  return {
    added,
    removed,
    changed,
    unchanged,
    hasChanges: added.length > 0 || removed.length > 0 || changed.length > 0,
  };
}

/**
 * Calendar keys that no longer describe a real exam, so the backend can delete
 * their events on the next sync.
 *
 * This matters because the key is derived from course code + date: when an exam
 * moves, syncing alone would create a NEW event and leave the old date sitting
 * in the student's calendar as a phantom exam. Dropped courses have the same
 * problem.
 */
export async function staleKeysFor(diff) {
  if (!diff) return [];
  const obsolete = [
    ...diff.removed.map((exam) => exam),
    // Only a date change orphans an event — the key is code+date, so a time or
    // venue edit updates the existing event in place.
    ...diff.changed
      .filter((entry) => entry.changes.some((c) => c.field === "date"))
      .map((entry) => entry.before),
  ];
  return Promise.all(obsolete.map((exam) => stableKey(exam)));
}

/** Discard a stored schedule (cascades to its exams). */
export async function deleteSchedule(scheduleId) {
  if (!scheduleId) return;
  const { error } = await supabase.from("schedules").delete().eq("id", scheduleId);
  if (error) throw error;
}
