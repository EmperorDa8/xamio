import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase } from "../../lib/supabase";
import SiteNav, { Brand } from "../SiteNav";
import "./admin.css";

/**
 * Founder dashboard at /admin.
 *
 * Every number comes from one RPC, public.admin_dashboard(), which checks the
 * caller against xamio.admins IN THE DATABASE and raises for anyone else. This
 * component never decides who is allowed; it only renders what the database
 * agreed to return, and shows a plain "not found" otherwise.
 *
 * Your own (admin) activity is excluded from every metric except the users
 * table and the event feed, where it's marked "you".
 */

const RANGES = [30, 60, 90];
const METRICS = [
  { key: "active", label: "Active users" },
  { key: "signups", label: "Sign-ups" },
  { key: "uploads", label: "Uploads" },
  { key: "syncs", label: "Calendar syncs" },
];
const EVENT_LABEL = { app_open: "Opened the app", upload: "Uploaded a timetable", sync: "Added to calendar", signup: "Signed up" };

const fmtDate = (d) => (d ? new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "—");
const fmtDateTime = (d) =>
  d ? new Date(d).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const pct = (n, d) => (d ? Math.round((n / d) * 100) : 0);

function ago(d) {
  if (!d) return "never";
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function Stat({ label, value, sub, tone }) {
  return (
    <div className={`ad-stat${tone ? ` tone-${tone}` : ""}`}>
      <span className="ad-stat-label">{label}</span>
      <span className="ad-stat-value">{value}</span>
      {sub && <span className="ad-stat-sub">{sub}</span>}
    </div>
  );
}

function Funnel({ steps }) {
  const top = steps[0]?.n || 0;
  return (
    <ol className="ad-funnel">
      {steps.map((s, i) => (
        <li key={s.step}>
          <span className="ad-funnel-step">{s.step}</span>
          <span className="ad-funnel-bar" aria-hidden="true">
            <i style={{ width: `${top ? Math.max((s.n / top) * 100, s.n ? 3 : 0) : 0}%` }} />
          </span>
          <span className="ad-funnel-n">
            <b>{s.n}</b> {i > 0 && <small>{pct(s.n, top)}%</small>}
          </span>
        </li>
      ))}
    </ol>
  );
}

function DailyChart({ daily, metric }) {
  const [hover, setHover] = useState(null);
  const values = daily.map((d) => d[metric] || 0);
  const max = Math.max(1, ...values);
  const W = 720;
  const H = 180;
  const pad = { l: 28, r: 8, t: 10, b: 22 };
  const bw = (W - pad.l - pad.r) / Math.max(daily.length, 1);
  const ticks = max <= 4 ? Array.from({ length: max + 1 }, (_, i) => i) : [0, Math.round(max / 2), max];
  const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const total = values.reduce((a, b) => a + b, 0);

  return (
    <div className="ad-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${METRICS.find((m) => m.key === metric).label} per day, total ${total}`}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="ad-grid" />
            <text x={pad.l - 6} y={y(t) + 4} className="ad-axis" textAnchor="end">{t}</text>
          </g>
        ))}
        {daily.map((d, i) => {
          const v = d[metric] || 0;
          const x = pad.l + i * bw;
          const h = (H - pad.t - pad.b) * (v / max);
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)}>
              {/* hit target is the full column, bigger than the mark */}
              <rect x={x} y={pad.t} width={bw} height={H - pad.t - pad.b} fill="transparent" />
              {v > 0 && (
                <rect x={x + 1} y={y(v)} width={Math.max(bw - 2, 1)} height={h} rx={Math.min(3, bw / 3)}
                  className={`ad-bar${hover === i ? " on" : ""}`} />
              )}
            </g>
          );
        })}
        {daily.filter((_, i) => i % Math.ceil(daily.length / 6) === 0).map((d) => {
          const i = daily.indexOf(d);
          return <text key={d.day} x={pad.l + i * bw + bw / 2} y={H - 6} className="ad-axis" textAnchor="middle">{fmtDate(d.day)}</text>;
        })}
      </svg>
      {hover !== null && (
        <div className="ad-tip" style={{ left: `${((pad.l + hover * bw + bw / 2) / W) * 100}%` }}>
          <b>{fmtDate(daily[hover].day)}</b>
          {METRICS.map((m) => (
            <span key={m.key} className={m.key === metric ? "on" : ""}>{m.label}: {daily[hover][m.key] || 0}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function Retention({ cohorts }) {
  if (!cohorts.length) return <p className="ad-empty">No cohorts yet.</p>;
  return (
    <div className="ad-scroll">
      <table className="ad-retention">
        <thead>
          <tr>
            <th>Cohort (week of)</th>
            <th>Users</th>
            {Array.from({ length: 9 }, (_, w) => <th key={w}>W{w}</th>)}
          </tr>
        </thead>
        <tbody>
          {cohorts.map((c) => (
            <tr key={c.cohort}>
              <td>{fmtDate(c.cohort)}</td>
              <td>{c.size}</td>
              {c.weeks.map((n, w) => {
                const p = pct(n, c.size);
                const future = new Date(c.cohort).getTime() + w * 7 * 86400000 > Date.now();
                return (
                  <td key={w} className={future ? "future" : ""} title={future ? "Not reached yet" : `${n} of ${c.size} active in week ${w}`}
                    style={future ? undefined : { "--p": p / 100 }}>
                    {future ? "" : `${p}%`}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Reminders({ r }) {
  const s = r.by_status || {};
  let lastRun = null;
  try {
    lastRun = r.dispatch_last?.body ? JSON.parse(r.dispatch_last.body) : null;
  } catch {
    lastRun = null;
  }
  const lastOk = r.dispatch_last?.status === 200;
  const stale = r.dispatch_last?.at && Date.now() - new Date(r.dispatch_last.at).getTime() > 5 * 60000;
  return (
    <div className="ad-rem">
      <div className="ad-rem-row">
        {["pending", "sending", "sent", "failed", "cancelled"].map((k) => (
          <div key={k} className={`ad-pill st-${k}`}><b>{s[k] || 0}</b>{k}</div>
        ))}
      </div>
      <dl className="ad-dl">
        <dt>Dispatcher</dt>
        <dd>
          <span className={`ad-status ${lastOk && !stale ? "ok" : "bad"}`}>
            {lastOk && !stale ? "● Healthy" : "▲ Needs a look"}
          </span>{" "}
          last run {ago(r.dispatch_last?.at)} · HTTP {r.dispatch_last?.status ?? "—"}
          {lastRun && <small> · sent {lastRun.sent}, failed {lastRun.failed}, skipped stale {lastRun.skipped_stale ?? 0}</small>}
        </dd>
        <dt>Next due</dt><dd>{fmtDateTime(r.next_due)}</dd>
        <dt>Overdue</dt>
        <dd>{r.overdue ? <span className="ad-status bad">▲ {r.overdue} overdue</span> : <span className="ad-status ok">● none</span>}</dd>
        <dt>Recipients</dt><dd>{r.recipients}</dd>
      </dl>
      {r.recent_errors?.length > 0 && (() => {
        // Cancelled rows carry the reason they were skipped (e.g. the exam had
        // already happened) — expected behaviour, not a failure.
        const failures = r.recent_errors.filter((e) => e.status !== "cancelled").length;
        return (
          <details className={`ad-errors${failures ? " has-fail" : ""}`}>
            <summary>
              {failures ? `${failures} failed send${failures > 1 ? "s" : ""} · ` : ""}
              {r.recent_errors.length - failures} skipped (stale or cancelled)
            </summary>
            <ul>{r.recent_errors.map((e, i) => <li key={i}><time>{fmtDateTime(e.at)}</time> <b className={`st-${e.status}`}>{e.status}</b> {e.error}</li>)}</ul>
          </details>
        );
      })()}
    </div>
  );
}

const KIND_LABEL = { exam: "Exams", test: "Tests", coursework: "Assignments", problem_set: "Homework", milestone: "Milestones", meeting: "Meetings" };

function Bars({ data, empty, labels = {} }) {
  const entries = Object.entries(data || {})
    .map(([k, n]) => [labels[k] || k, n])
    .sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <p className="ad-empty">{empty}</p>;
  const max = Math.max(...entries.map(([, n]) => n));
  return (
    <ul className="ad-bars">
      {entries.map(([k, n]) => (
        <li key={k}>
          <span>{k}</span>
          <span className="ad-funnel-bar"><i style={{ width: `${(n / max) * 100}%` }} /></span>
          <b>{n}</b>
        </li>
      ))}
    </ul>
  );
}

export default function AdminDashboard({ onExit }) {
  const [days, setDays] = useState(60);
  const [metric, setMetric] = useState("active");
  const [data, setData] = useState(null);
  const [state, setState] = useState("loading"); // loading | ready | denied | error
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    const { data: d, error: e } = await supabase.rpc("admin_dashboard", { days });
    if (e) {
      // P0002 = not an admin; 42501 = not signed in. Both render as "not found".
      if (e.code === "P0002" || e.code === "42501" || /not found/i.test(e.message || "")) setState("denied");
      else {
        setError(e.message);
        setState((s) => (s === "ready" ? s : "error"));
      }
      return;
    }
    setData(d);
    setError(null);
    setState("ready");
  }, [days]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);

  const t = data?.totals;
  const funnel = data?.funnel || [];
  const uploaded = funnel.find((f) => f.step === "Uploaded a timetable")?.n || 0;
  const daily = useMemo(() => data?.daily || [], [data]);

  if (state === "denied") {
    return (
      <div className="x-app">
        <SiteNav brand={<Brand onClick={onExit} />} />
        <main className="x-page ad-404">
          <h1>Page not found<span className="coral">.</span></h1>
          <button className="btn btn-ghost" onClick={onExit}>Back to Xamio</button>
        </main>
      </div>
    );
  }

  return (
    <div className="x-app ad">
      <div className="x-ambient" aria-hidden="true" />
      <SiteNav
        brand={<Brand onClick={onExit} />}
        actions={
          <>
            <span className="ad-live">
              <span className="x-pulse" /> {data ? `Updated ${ago(data.generated_at)}` : "Loading…"}
            </span>
            <button className="x-nav-link" onClick={load}>Refresh</button>
            <button className="x-nav-link" onClick={onExit}>Back to app</button>
          </>
        }
      />

      <main className="x-page ad-page">
        <div className="x-pagehead">
          <div>
            <span className="x-tag"><span className="x-dot" /> Founder view · admins only</span>
            <h1>How Xamio is <em>doing</em>.</h1>
            <p>Your own account is left out of every number except the users table and the event feed.</p>
          </div>
          <div className="ad-range" role="radiogroup" aria-label="Date range">
            {RANGES.map((r) => (
              <button key={r} role="radio" aria-checked={days === r} className={`preset${days === r ? " on" : ""}`} onClick={() => setDays(r)}>
                {r} days
              </button>
            ))}
          </div>
        </div>

        {state === "error" && <div className="alert error"><span className="glyph">!</span><span>Couldn't load the dashboard: {error}</span></div>}
        {state === "loading" && <p className="ad-empty">Loading…</p>}

        {data && (
          <>
            {error && <div className="alert warn"><span className="glyph">!</span><span>Last refresh failed: {error}. Showing the previous numbers.</span></div>}

            <section className="ad-stats">
              <Stat label="Real users" value={t.real_users} sub={`${t.accounts} accounts · ${t.admins} admin`} tone="lavender" />
              <Stat label="New sign-ups" value={t.signups_30d} sub={`${t.signups_7d} in the last 7 days`} tone="pink" />
              <Stat label="Active users" value={t.wau} sub={`7 days · ${t.mau} in 30 · ${t.dau} today`} tone="mint" />
              <Stat label="Activation" value={`${pct(uploaded, t.real_users)}%`} sub={`${uploaded} of ${t.real_users} uploaded a timetable`} tone="peach" />
              <Stat label="Saved schedules" value={t.schedules} sub={`${t.items} items`} tone="sky" />
            </section>

            <div className="ad-grid2">
              <section className="ad-card">
                <h2>Funnel</h2>
                <p className="ad-note">Share of everyone who signed up.</p>
                <Funnel steps={funnel} />
              </section>

              <section className="ad-card">
                <h2>Reminder pipeline</h2>
                <p className="ad-note">The queue behind every email reminder, all users.</p>
                <Reminders r={data.reminders} />
              </section>
            </div>

            <section className="ad-card">
              <div className="ad-card-head">
                <div>
                  <h2>Daily activity</h2>
                  <p className="ad-note">Last {data.days} days. Hover a day for every metric.</p>
                </div>
                <div className="ad-range" role="radiogroup" aria-label="Metric">
                  {METRICS.map((m) => (
                    <button key={m.key} role="radio" aria-checked={metric === m.key} className={`preset${metric === m.key ? " on" : ""}`} onClick={() => setMetric(m.key)}>
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
              <DailyChart daily={daily} metric={metric} />
            </section>

            <section className="ad-card">
              <h2>Weekly retention</h2>
              <p className="ad-note">Share of each sign-up week still active N weeks later. Darker = more retained.</p>
              <Retention cohorts={data.retention || []} />
            </section>

            <div className="ad-grid2">
              <section className="ad-card">
                <h2>What people upload</h2>
                <p className="ad-note">Saved items by type, and which parser read them.</p>
                <Bars data={data.content.items_by_kind} labels={KIND_LABEL} empty="No saved items yet." />
                <div style={{ height: 14 }} />
                <Bars data={data.content.parsers} empty="No schedules yet." />
              </section>

              <section className="ad-card">
                <h2>Live activity</h2>
                <p className="ad-note">Latest 30 events.</p>
                <ul className="ad-feed">
                  {data.recent.map((e, i) => (
                    <li key={i} className={e.is_admin ? "admin" : ""}>
                      <span className={`ad-ev ev-${e.event}`}>{EVENT_LABEL[e.event] || e.event}</span>
                      <span className="ad-who">{e.is_admin ? "you" : e.who}</span>
                      <time>{ago(e.at)}</time>
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            <section className="ad-card">
              <h2>Users</h2>
              <p className="ad-note">Emails are masked. Newest first.</p>
              <div className="ad-scroll">
                <table className="ad-table">
                  <thead>
                    <tr>
                      <th>User</th><th>Signed up</th><th>Last seen</th><th>Active days</th>
                      <th>Uploads</th><th>Syncs</th><th>Schedules</th><th>Reminders queued</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.users.map((u, i) => (
                      <tr key={i} className={u.is_admin ? "admin" : ""}>
                        <td><span className="ad-mono">{u.label}</span>{u.is_admin && <span className="ad-you">you</span>}</td>
                        <td>{fmtDate(u.signed_up)}</td>
                        <td>{ago(u.last_seen)}</td>
                        <td>{u.active_days}</td>
                        <td>{u.uploads || <span className="ad-zero">0</span>}</td>
                        <td>{u.syncs || <span className="ad-zero">0</span>}</td>
                        <td>{u.schedules || <span className="ad-zero">0</span>}</td>
                        <td>{u.reminders_pending || <span className="ad-zero">0</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
