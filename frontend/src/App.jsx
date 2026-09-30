import { Suspense, lazy, useEffect, useState } from "react";
import axios from "axios";
import { supabase } from "./lib/supabase";
import UploadZone from "./components/UploadZone";
import ExamTable from "./components/ExamTable";
import ReminderConfig from "./components/ReminderConfig";
import CalendarSync from "./components/CalendarSync";
import PublicSite from "./components/PublicSite";
import Dashboard from "./components/Dashboard";
import ChangeSummary from "./components/ChangeSummary";
import SiteNav, { Brand, SiteFooter } from "./components/SiteNav";
import { useAuth } from "./auth/AuthProvider";
import { logEvent } from "./lib/analytics";

// Loaded only when someone opens /admin, so regular users never download it.
// Access itself is enforced by the admin_dashboard() RPC, not by this split.
const AdminDashboard = lazy(() => import("./components/admin/AdminDashboard"));
import {
  saveSchedule,
  loadLatestSchedule,
  replaceScheduleExams,
  diffSchedules,
  staleKeysFor,
} from "./lib/schedules";

const API = import.meta.env.VITE_API_URL || "/api";
// Same names and colours as the "How it works" cards on the landing page, so
// the app picks up exactly where the pitch left off.
const STEPS = [
  { label: "Upload", hint: "Timetable + courses", tone: "pink" },
  { label: "Review", hint: "Check what we found", tone: "peach" },
  { label: "Alerts", hint: "When to nudge you", tone: "lavender" },
  { label: "Sync", hint: "Calendar + email", tone: "mint" },
];
const PAGE_HEADS = [
  {
    title: <>Let's find your <em>exams</em>.</>,
    sub: "Add the courses you're registered for, then drop the timetable exactly as your faculty sent it.",
  },
  {
    title: <>Check what we <em>found</em>.</>,
    sub: "Click any cell to fix it. Untick anything you don't want on your calendar.",
  },
  {
    title: <>When should we <em>nudge</em> you?</>,
    sub: "Smart reminders pick the timing from what each item is. Or set one schedule for everything.",
  },
  {
    title: <>Put it on your <em>calendar</em>.</>,
    sub: "Google Calendar, any other calendar app, and email reminders. Use one or all three.",
  },
];

export default function App() {
  const { user, loading: authLoading, signOut } = useAuth();
  const [path, setPath] = useState(() => window.location.pathname);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Only decides whether to SHOW the Admin link; the data is gated server-side.
  useEffect(() => {
    if (!user) return setIsAdmin(false);
    supabase.rpc("am_i_admin").then(({ data }) => setIsAdmin(data === true), () => setIsAdmin(false));
  }, [user]);

  const goTo = (to) => {
    window.history.pushState({}, "", to);
    setPath(to);
    window.scrollTo({ top: 0 });
  };
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [exams, setExams] = useState([]);
  const [selectedExams, setSelectedExams] = useState([]);
  const [registeredCourses, setRegisteredCourses] = useState([]);
  const [unmatchedCourses, setUnmatchedCourses] = useState([]);
  const [reminders, setReminders] = useState([1440, 180]);
  // "smart" derives each item's reminders from its type; "custom" applies the
  // picks above to everything. Smart by default — the students who miss
  // deadlines are exactly the ones who never open a settings screen.
  const [reminderMode, setReminderMode] = useState("smart");
  const [synced, setSynced] = useState(false);
  const [modelUsed, setModelUsed] = useState(null);
  const [dateWarnings, setDateWarnings] = useState([]);
  // Persistence: id of the stored schedule backing what's on screen, plus a
  // note when this session's state came from the database rather than an upload.
  const [scheduleId, setScheduleId] = useState(null);
  const [restoredAt, setRestoredAt] = useState(null);
  const [startedFresh, setStartedFresh] = useState(false);
  // "dashboard" is the home for someone who already has a schedule; "wizard" is
  // the upload → review → alerts → sync flow.
  const [view, setView] = useState("wizard");
  // What a re-upload changed versus the stored timetable, plus the calendar keys
  // that re-syncing needs to delete.
  const [changes, setChanges] = useState(null);
  const [staleKeys, setStaleKeys] = useState([]);
  // The schedule as it stood before this upload — kept so a re-upload can be
  // diffed against it rather than silently overwriting it.
  const [previousExams, setPreviousExams] = useState([]);

  // Bring back the last schedule this user parsed. Without this, a refresh (or
  // simply returning the next day) dropped everything and forced a re-upload —
  // and another AI parse — for data we already had.
  useEffect(() => {
    if (!user || startedFresh || exams.length > 0) return;
    let cancelled = false;

    (async () => {
      try {
        const saved = await loadLatestSchedule();
        if (cancelled || !saved?.exams?.length) return;
        setExams(saved.exams);
        setSelectedExams(saved.exams);
        setPreviousExams(saved.exams);
        setRegisteredCourses(saved.registeredCourses);
        setUnmatchedCourses(saved.unmatchedCourses);
        setDateWarnings(saved.dateWarnings);
        setModelUsed(saved.modelUsed);
        setScheduleId(saved.id);
        setRestoredAt(saved.createdAt);
        setView("dashboard"); // a countdown home, not the upload box
      } catch (err) {
        // Restoring is a convenience — never block the upload flow over it.
        console.debug("Could not restore saved schedule:", err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user, startedFresh, exams.length]);

  const handleUpload = async (file, courseContext = {}) => {
    setLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (courseContext.coursesText?.trim()) form.append("courses_text", courseContext.coursesText.trim());
      if (courseContext.coursesFile) form.append("courses_file", courseContext.coursesFile);

      const { data } = await axios.post(`${API}/parse`, form);
      setExams(data.exams);
      setSelectedExams(data.exams);
      setRegisteredCourses(data.registered_courses || []);
      setUnmatchedCourses(data.unmatched_courses || []);
      setModelUsed(data.model_used || null);
      setDateWarnings(data.date_warnings || []);

      // Re-uploading a revised timetable should say what moved, not quietly
      // swap the schedule. Also collect the calendar keys that are now orphaned
      // so the next sync can delete those events.
      const diff = previousExams.length
        ? diffSchedules(previousExams, data.exams || [])
        : null;
      setChanges(diff?.hasChanges ? diff : null);
      setStaleKeys(diff?.hasChanges ? await staleKeysFor(diff) : []);

      logEvent("upload", {
        exams: data.exams?.length ?? 0,
        model: data.model_used || null,
        changed: diff?.hasChanges ? diff.changed.length : 0,
        added: diff?.hasChanges ? diff.added.length : 0,
        removed: diff?.hasChanges ? diff.removed.length : 0,
      });

      // Persist immediately, so the parse survives a refresh even if the user
      // never finishes the wizard. A failure here costs them persistence, not
      // the flow — the parsed exams are already in state.
      setRestoredAt(null);
      try {
        setScheduleId(
          await saveSchedule({
            exams: data.exams || [],
            registeredCourses: data.registered_courses || [],
            unmatchedCourses: data.unmatched_courses || [],
            dateWarnings: data.date_warnings || [],
            modelUsed: data.model_used || null,
            sourceFilename: file?.name || null,
          }),
        );
      } catch (err) {
        console.debug("Could not save schedule:", err);
        setScheduleId(null);
      }

      setPreviousExams(data.exams || []);
      setView("wizard");
      setStep(1);
    } catch (e) {
      let msg;
      if (e.response) {
        // Backend responded with an error — show its reason.
        if (e.response.status === 429) {
          msg = "Too many uploads in a short time. Wait a minute and try again.";
        } else {
          msg =
            e.response.data?.detail ||
            e.response.data?.error ||
            `Server error (${e.response.status}). Check the backend logs.`;
        }
      } else {
        // No response at all — the request never reached the backend.
        msg =
          "Can't reach the server. Is the backend running and is VITE_API_URL pointing to it?";
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleExamChange = (updated, selSet) => {
    setExams(updated);
    setSelectedExams(selSet ? updated.filter((_, i) => selSet.has(i)) : updated);
  };

  // Leaving Review is the point the user has signed off on the rows, so that's
  // what we store — otherwise their date corrections would be lost on refresh.
  const handleReviewContinue = async () => {
    if (scheduleId) {
      try {
        await replaceScheduleExams(scheduleId, exams);
      } catch (err) {
        console.debug("Could not save edited exams:", err);
      }
    }
    setStep(2);
  };

  const reset = () => {
    setStep(0);
    setExams([]);
    setSelectedExams([]);
    setRegisteredCourses([]);
    setUnmatchedCourses([]);
    setReminders([1440, 180]);
    setSynced(false);
    setModelUsed(null);
    setDateWarnings([]);
    setError(null);
    setScheduleId(null);
    setRestoredAt(null);
    setChanges(null);
    setStaleKeys([]);
    setPreviousExams([]);
    setView("wizard");
    // Starting over is a deliberate "give me the upload screen", so don't let
    // the restore effect immediately pull the old schedule back in. The stored
    // copy stays put until a new upload replaces it.
    setStartedFresh(true);
  };

  /** From the dashboard: upload a revised timetable. Unlike "start over" this
   *  KEEPS the current schedule in memory, because that is exactly what the new
   *  upload gets diffed against. */
  const startNewUpload = () => {
    setChanges(null);
    setStaleKeys([]);
    setError(null);
    setSynced(false);
    setView("wizard");
    setStep(0);
  };

  /** Back to the countdown home — only meaningful once a schedule exists. */
  const goToDashboard = () => {
    setView("dashboard");
    setError(null);
  };

  const modelLabel = modelUsed
    ? modelUsed.toLowerCase().includes("gemini")
      ? "Gemini 2.5 Flash"
      : modelUsed.toLowerCase().includes("manual")
        ? "Manual extractor (offline)"
        : `OpenRouter (${modelUsed})`
    : null;

  // Auth gate: the whole app requires a signed-in user.
  if (authLoading) {
    return (
      <div className="auth-wrap">
        <div className="x-ambient" aria-hidden="true" />
        <div className="auth-loading"><span className="x-pulse" /> Loading your schedule…</div>
      </div>
    );
  }
  if (!user) return <PublicSite />;

  if (path.replace(/\/+$/, "") === "/admin") {
    return (
      <Suspense fallback={<div className="auth-wrap"><div className="auth-loading"><span className="x-pulse" /> Loading…</div></div>}>
        <AdminDashboard onExit={() => goTo("/")} />
      </Suspense>
    );
  }

  const head = PAGE_HEADS[step];
  const initial = (user.user_metadata?.full_name || user.email || "?").trim()[0];

  return (
    <div className="x-app">
      <div className="x-ambient" aria-hidden="true" />

      <SiteNav
        brand={
          <Brand
            // With a schedule in hand, home is the countdown — not a blank
            // upload box that throws away what we already parsed.
            onClick={() => (exams.length > 0 ? goToDashboard() : reset())}
          />
        }
        actions={
          <>
            {view === "wizard" && exams.length > 0 && (
              <button className="x-nav-link" onClick={goToDashboard}>My schedule</button>
            )}
            {view === "wizard" && step > 0 && (
              <button className="x-nav-link" onClick={reset}>Start over</button>
            )}
            {isAdmin && (
              <button className="x-nav-link" onClick={() => goTo("/admin")}>Admin</button>
            )}
            <span className="x-user">
              <span className="x-avatar" aria-hidden="true">{initial}</span>
              <span className="x-user-email">{user.email}</span>
              <button className="x-nav-link" onClick={signOut}>Sign out</button>
            </span>
          </>
        }
      />

      <main className="x-page">
          {view === "dashboard" ? (
            <Dashboard
              exams={exams}
              restoredAt={restoredAt}
              onReview={() => { setView("wizard"); setStep(1); }}
              onSync={() => { setView("wizard"); setStep(3); }}
              onNewUpload={startNewUpload}
            />
          ) : (
          <>
          <nav className="x-steps" aria-label="Progress">
            {STEPS.map((s, i) => (
              <button
                key={s.label}
                type="button"
                className={`x-step tone-${s.tone}${i === step ? " active" : i < step ? " done" : ""}`}
                aria-current={i === step ? "step" : undefined}
                // Only completed steps are revisitable; jumping ahead would skip
                // the review the later steps depend on.
                disabled={i > step}
                onClick={() => i < step && setStep(i)}
              >
                <span className="x-step-n">{i < step ? "✓" : `0${i + 1}`}</span>
                <span>
                  {s.label}
                  <small>{s.hint}</small>
                </span>
              </button>
            ))}
          </nav>

          <div className="x-pagehead x-rise" key={step}>
            <div>
              <span className="x-tag"><span className="x-dot" /> Step {step + 1} of {STEPS.length}</span>
              <h1>{head.title}</h1>
              <p>{head.sub}</p>
            </div>
          </div>

          {step === 0 && (
            <div className="stack-lg">
              <UploadZone onUpload={handleUpload} loading={loading} />
              {error && <div className="alert error"><span className="glyph">!</span><span>{error}</span></div>}
            </div>
          )}

          {step === 1 && (
            <div className="stack-lg">
              {restoredAt && (
                <div className="alert info">
                  <span className="glyph">↺</span>
                  <div>
                    <b>Picked up where you left off.</b>{" "}
                    <span>
                      This is the timetable you uploaded on{" "}
                      {new Date(restoredAt).toLocaleDateString(undefined, {
                        day: "numeric", month: "long", year: "numeric",
                      })}
                      .
                    </span>{" "}
                    <button className="link-back" onClick={startNewUpload}>
                      Upload a revised one
                    </button>
                  </div>
                </div>
              )}
              <ChangeSummary diff={changes} onDismiss={() => setChanges(null)} />
              {modelLabel && (
                <span className="coord">parsed by {modelLabel}</span>
              )}
              {dateWarnings.length > 0 && (
                <div className="alert warn">
                  <span className="glyph">⚠</span>
                  <div>
                    <b>{dateWarnings.length} date{dateWarnings.length > 1 ? "s" : ""} checked against your timetable.</b>
                    <ul className="warn-list">
                      {dateWarnings.map((w, i) => <li key={i}>{w}</li>)}
                    </ul>
                    <span className="coord">Rows flagged with ⚠ couldn't be auto-confirmed — please verify before syncing.</span>
                  </div>
                </div>
              )}
              {(registeredCourses.length > 0 || unmatchedCourses.length > 0) && (
                <div className="match-summary">
                  <div>
                    <span className="coord">course matching</span>
                    <p>
                      <b>{selectedExams.length}</b> exam{selectedExams.length !== 1 ? "s" : ""} matched
                      {registeredCourses.length > 0 ? ` from ${registeredCourses.length} registered course${registeredCourses.length === 1 ? "" : "s"}` : ""}.
                    </p>
                  </div>
                  {unmatchedCourses.length > 0 && (
                    <div className="unmatched">
                      <span className="coord">not found</span>
                      <p>{unmatchedCourses.join(", ")}</p>
                    </div>
                  )}
                </div>
              )}
              <ExamTable exams={exams} onChange={handleExamChange} />
              <div className="row-end">
                <button className="btn btn-primary" disabled={selectedExams.length === 0} onClick={handleReviewContinue}>
                  Continue
                  <span className="arrow"><svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6" /></svg></span>
                </button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="stack-lg">
              <div className="panel">
                <p className="big">
                  <b>{selectedExams.length} exam{selectedExams.length !== 1 ? "s" : ""}</b> ready to schedule.
                </p>
              </div>
              <ReminderConfig
                reminders={reminders}
                onChange={setReminders}
                mode={reminderMode}
                onModeChange={setReminderMode}
              />
              <div className="row-between">
                <button className="link-back" onClick={() => setStep(1)}>Back</button>
                <button
                  className="btn btn-primary"
                  disabled={reminderMode === "custom" && reminders.length === 0}
                  onClick={() => setStep(3)}
                >
                  Continue
                  <span className="arrow"><svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6" /></svg></span>
                </button>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="stack-lg">
              {synced && (
                <div className="panel win x-rise">
                  <p className="big"><b>You're all set.</b> <em>Good luck on your exams.</em></p>
                </div>
              )}
              <CalendarSync
                exams={selectedExams}
                reminders={reminders}
                reminderMode={reminderMode}
                defaultEmail={user.email || ""}
                staleKeys={staleKeys}
                onSuccess={(channel) => {
                  setSynced(true);
                  // Events for moved/dropped exams are cleared on the first
                  // sync; keeping the keys would re-delete on every later sync.
                  setStaleKeys([]);
                  logEvent("sync", { exams: selectedExams.length, channel: channel || null });
                }}
              />
              <div className="row-between">
                <button className="link-back" onClick={() => setStep(2)}>Back</button>
                {exams.length > 0 && (
                  <button className="link-back" onClick={goToDashboard}>My schedule</button>
                )}
              </div>
            </div>
          )}
          </>
          )}
      </main>
      <SiteFooter />
    </div>
  );
}
