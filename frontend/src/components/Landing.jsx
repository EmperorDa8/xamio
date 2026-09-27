import { useState } from "react";
import "./landing/landing.css";
import HeroScene from "./landing/HeroScene";
import TryIt from "./landing/TryIt";
import SiteNav, { Brand, SiteFooter } from "./SiteNav";
import { useReveal, useTilt } from "./landing/hooks";
import { IconScan, IconTarget, IconCalendar, IconBell, IconArrow, IconDownload, IconLayers } from "./icons";

const FORMATS = ["PDF", "PNG", "JPG", "XLSX", "CSV", "DOCX", "TXT", "Google Calendar", ".ics", "Email"];

const STEPS = [
  { n: "01", t: "Upload", d: "Drop the timetable exactly as the faculty sent it — PDF, photo, spreadsheet, Word. Add your registered courses.", tone: "pink" },
  { n: "02", t: "Review", d: "AI pulls out every exam and keeps only the ones you take. Fix anything before it goes anywhere.", tone: "peach" },
  { n: "03", t: "Alerts", d: "Smart reminders by type, or pick your own — the day before, 3 hours before, whatever keeps you calm.", tone: "lavender" },
  { n: "04", t: "Sync", d: "Straight into Google Calendar, or download a .ics for any calendar app. Emails land on time.", tone: "mint" },
];

function TiltCard({ className = "", children, max = 6, ...rest }) {
  const ref = useTilt(max);
  return (
    <article ref={ref} className={`x-tilt ${className}`} {...rest}>
      <div className="x-tilt-inner">{children}</div>
      <span className="x-glare" aria-hidden="true" />
    </article>
  );
}

function EducatorInvite() {
  const [copied, setCopied] = useState(false);
  const url = typeof window !== "undefined" ? window.location.origin : "https://xamio.app";
  const message =
    `Hi all — the exam timetable is out. If you'd rather not dig through it, ` +
    `Xamio (${url}) reads the file, keeps only the courses you're registered for, ` +
    `and reminds you before each exam. It's free.`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
      setTimeout(() => setCopied(false), 2200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="ed-msg">
      <div className="ed-msg-head">
        <span className="ed-dot" /> Message to your class
      </div>
      <p>{message}</p>
      <button type="button" className="x-btn x-btn-dark" onClick={copy}>
        {copied ? "Copied — paste it in your class group" : "Copy message"}
      </button>
    </div>
  );
}

export default function Landing({ onStart }) {
  const revealRef = useReveal();

  return (
    <div className="xl" ref={revealRef}>
      <SiteNav
        brand={<Brand />}
        links={
          <>
            <a href="#try">Try it</a>
            <a href="#how">How it works</a>
            <a href="#educators">For educators</a>
          </>
        }
        actions={
          <>
            <button className="x-nav-link" onClick={() => onStart("signin")}>Sign in</button>
            <button className="x-btn x-btn-primary x-btn-sm" onClick={() => onStart("signup")}>Get started</button>
          </>
        }
      />

      <main id="top">
        {/* ───────── hero ───────── */}
        <section className="xl-hero">
          <div className="xl-hero-copy">
            <span className="xl-eyebrow">
              <span className="x-pulse" /> Free for students · early access
            </span>
            <h1>
              Never miss an <em>exam</em> again<span className="coral">.</span>
            </h1>
            <p className="xl-lead">
              The timetable is fourteen pages. Your exams fit on one card. Upload it, and Xamio
              finds yours, puts them in your calendar, and reminds you before each one.
            </p>
            <div className="xl-audience">
              <button className="x-btn x-btn-primary x-btn-lg" onClick={() => onStart("signup")}>
                I'm a student
                <span className="x-btn-arrow"><IconArrow /></span>
              </button>
              <a className="x-btn x-btn-ghost x-btn-lg" href="#educators">I'm an educator</a>
            </div>
            <ul className="xl-trust">
              <li>No credit card</li>
              <li>Any file format</li>
              <li>Under a minute</li>
            </ul>
          </div>
          <div className="xl-hero-art">
            <HeroScene />
          </div>
        </section>

        {/* ───────── formats marquee ───────── */}
        <div className="xl-marquee" aria-label="Supported formats and destinations">
          <div className="xl-marquee-track">
            {[...FORMATS, ...FORMATS].map((f, i) => (
              <span key={i} className={`xl-pill tone-${["pink", "peach", "lavender", "mint", "sky"][i % 5]}`}>{f}</span>
            ))}
          </div>
        </div>

        {/* ───────── interactive demo ───────── */}
        <section className="xl-section" id="try">
          <div className="xl-sec-head" data-reveal>
            <span className="x-tag">Try it — no sign-up</span>
            <h2>See your exams <em>find you</em>.</h2>
            <p>This is a real faculty-style timetable. Pick the courses you take and watch it shrink to just yours.</p>
          </div>
          <div data-reveal>
            <TryIt onStart={onStart} />
          </div>
        </section>

        {/* ───────── how it works ───────── */}
        <section className="xl-section" id="how">
          <div className="xl-sec-head" data-reveal>
            <span className="x-tag">How it works</span>
            <h2>Four steps. <em>About a minute.</em></h2>
          </div>
          <div className="xl-steps">
            {STEPS.map((s, i) => (
              <div key={s.n} className={`xl-step tone-${s.tone}`} data-reveal style={{ "--i": i }}>
                <span className="xl-step-n">{s.n}</span>
                <h3>{s.t}</h3>
                <p>{s.d}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ───────── features bento ───────── */}
        <section className="xl-section">
          <div className="xl-sec-head" data-reveal>
            <span className="x-tag">Why students stay</span>
            <h2>From a messy timetable to a <em>calm week</em>.</h2>
          </div>

          <div className="xl-bento">
            <TiltCard className="b-wide tone-pink" data-reveal>
              <span className="b-ico"><IconScan /></span>
              <h3>Reads any timetable</h3>
              <p>PDF, a photo of the notice board, spreadsheet, Word or plain text. AI reads it; an offline extractor backs it up.</p>
              <div className="b-files">
                {["PDF", "JPG", "XLSX", "DOCX", "CSV", "TXT"].map((f, i) => (
                  <span key={f} style={{ "--i": i }}>{f}</span>
                ))}
              </div>
            </TiltCard>

            <TiltCard className="tone-peach" data-reveal>
              <span className="b-ico"><IconTarget /></span>
              <h3>Only your exams</h3>
              <p>Add the courses you registered for. Everything else on the sheet disappears.</p>
            </TiltCard>

            <TiltCard className="tone-lavender" data-reveal>
              <span className="b-ico"><IconLayers /></span>
              <h3>Timetable changed? We catch it.</h3>
              <p>Re-upload the revised version and see exactly what moved. Your calendar gets fixed too.</p>
              <div className="b-diff">
                <span className="old">CSC 205 · Tue 9:00</span>
                <span className="new">CSC 205 · Thu 13:00</span>
              </div>
            </TiltCard>

            <TiltCard className="tone-mint" data-reveal>
              <span className="b-ico"><IconBell /></span>
              <h3>Reminders that know the difference</h3>
              <p>Exams get two weeks, one week, three days, the day before, then two hours with the room. Homework gets one nudge the evening before.</p>
              <div className="b-timeline">
                {["2w", "1w", "3d", "1d", "2h"].map((t, i) => (
                  <span key={t} style={{ "--i": i }}><i />{t}</span>
                ))}
              </div>
            </TiltCard>

            <TiltCard className="tone-sky" data-reveal>
              <span className="b-ico"><IconCalendar /></span>
              <h3>Google Calendar or .ics</h3>
              <p>One click into Google Calendar with titles, times and venues. Or export for Apple, Outlook, anything.</p>
            </TiltCard>

            <TiltCard className="b-dark b-full" data-reveal>
              <span className="b-ico"><IconDownload /></span>
              <h3>A countdown home</h3>
              <p>Open Xamio and see what's next, how long you've got, and where it is. Nothing else.</p>
              <div className="b-count"><b>4</b><span>days until<br />CSC 201</span></div>
            </TiltCard>
          </div>
        </section>

        {/* ───────── educators ───────── */}
        <section className="xl-section xl-edu" id="educators">
          <div className="xl-edu-copy" data-reveal>
            <span className="x-tag">For educators &amp; course reps</span>
            <h2>Publish the timetable once. <em>Stop answering</em> "when is the exam?"</h2>
            <p>
              Keep publishing the timetable the way you already do. Students drop that same file into Xamio,
              and each one gets a calendar and reminders for only their own papers. Nothing to set up on your side.
            </p>
            <ul className="xl-edu-list">
              <li><b>Fewer no-shows</b> from students who read the wrong row</li>
              <li><b>Changes reach them</b> when they re-upload the revised version</li>
              <li><b>Free for students</b>, so it's easy to recommend</li>
            </ul>
          </div>
          <div data-reveal>
            <EducatorInvite />
          </div>
        </section>

        {/* ───────── final CTA ───────── */}
        <section className="xl-final" data-reveal>
          <div className="xl-final-orbit" aria-hidden="true">
            <span className="tone-pink">CSC 201</span>
            <span className="tone-peach">MTH 211</span>
            <span className="tone-lavender">CSC 205</span>
            <span className="tone-mint">CSC 209</span>
          </div>
          <h2>Your next exam is already on the timetable.<br /><em>Put it on your calendar.</em></h2>
          <p>Free account. Any timetable. Reminders before every paper.</p>
          <button className="x-btn x-btn-primary x-btn-lg" onClick={() => onStart("signup")}>
            Get started — it's free
            <span className="x-btn-arrow"><IconArrow /></span>
          </button>
        </section>
      </main>

      <SiteFooter>
        <a
          href="https://productwatch.io/products/xamio?utm_source=badge"
          target="_blank"
          rel="noopener noreferrer"
          className="x-badge"
        >
          <img
            src="https://productwatch.io/backend/api/v1/badge/featured?productId=4f9886c3-3497-4eb7-8939-217f21e6ba3b&darkMode=false"
            alt="Xamio — featured on ProductWatch"
            width="260"
            height="54"
            loading="lazy"
          />
        </a>
      </SiteFooter>
    </div>
  );
}
