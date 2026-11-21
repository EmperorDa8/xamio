import { supabase } from "./supabase";

/**
 * Append a product-engagement event to public.activity_events.
 *
 * RLS guarantees a user can only write rows for their own user_id, so we read
 * the current user and stamp it explicitly. Failures are swallowed: analytics
 * must never break the product flow.
 *
 * These rows are what power the retention views (distinct active days per
 * user) on top of the single last_sign_in_at that Supabase auth keeps.
 *
 * Every event also carries a `client` object (country, os, browser, device)
 * for the audience breakdown on /admin. Country comes from /api/geo, which
 * reads the code Vercel derives from the request IP; the IP is never stored.
 */
export async function logEvent(eventType, metadata = {}) {
  try {
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) return;

    const client = { ...describeDevice(), country: await getCountry() };
    await supabase
      .from("activity_events")
      .insert({ user_id: user.id, event_type: eventType, metadata: { ...metadata, client } });
  } catch (err) {
    console.debug("logEvent failed (non-fatal):", err);
  }
}

const GEO_KEY = "xamio.country";
let geoPromise = null;

/** Two-letter country code, looked up once per tab session. Null if unknown. */
function getCountry() {
  try {
    const cached = sessionStorage.getItem(GEO_KEY);
    if (cached) return Promise.resolve(cached === "-" ? null : cached);
  } catch {
    // storage blocked; fall through to a lookup
  }
  if (!geoPromise) {
    const timeout = new Promise((resolve) => setTimeout(() => resolve(null), 2500));
    const lookup = fetch("/api/geo", { cache: "no-store" })
      .then((r) => (r.ok && r.headers.get("content-type")?.includes("json") ? r.json() : null))
      .then((d) => (d && /^[A-Z]{2}$/.test(d.country || "") ? d.country : null))
      .catch(() => null);
    geoPromise = Promise.race([lookup, timeout]).then((country) => {
      try {
        sessionStorage.setItem(GEO_KEY, country || "-");
      } catch {
        // ignore
      }
      return country;
    });
  }
  return geoPromise;
}

/** Coarse OS / browser / device type from the user agent. No fingerprinting. */
export function describeDevice(ua = typeof navigator !== "undefined" ? navigator.userAgent : "") {
  const os =
    /Windows/i.test(ua) ? "Windows"
    : /iPhone|iPad|iPod/i.test(ua) ? "iOS"
    : /Macintosh/i.test(ua) && typeof navigator !== "undefined" && navigator.maxTouchPoints > 1 ? "iOS" // iPadOS
    : /Mac OS X|Macintosh/i.test(ua) ? "macOS"
    : /Android/i.test(ua) ? "Android"
    : /CrOS/i.test(ua) ? "ChromeOS"
    : /Linux/i.test(ua) ? "Linux"
    : "Other";

  const browser =
    /Edg\//i.test(ua) ? "Edge"
    : /OPR\/|Opera/i.test(ua) ? "Opera"
    : /SamsungBrowser/i.test(ua) ? "Samsung Internet"
    : /Firefox|FxiOS/i.test(ua) ? "Firefox"
    : /Chrome|CriOS/i.test(ua) ? "Chrome"
    : /Safari/i.test(ua) ? "Safari"
    : "Other";

  const device =
    /iPad|Tablet/i.test(ua) || (/Android/i.test(ua) && !/Mobile/i.test(ua)) ? "Tablet"
    : /Mobi|iPhone|iPod|Android/i.test(ua) ? "Mobile"
    : os === "iOS" ? "Tablet"
    : "Desktop";

  return { os, browser, device };
}
