// GET /api/geo -> { country: "NG" | null }
//
// Vercel stamps every request with the visitor's country from their IP
// (x-vercel-ip-country), so this needs no third-party lookup and the IP itself
// is never stored. Only the two-letter country code leaves this function.
// The frontend calls it once per session and adds the code to its analytics
// events; see src/lib/analytics.js.
export default function handler(req, res) {
  const raw = req.headers["x-vercel-ip-country"];
  const country = typeof raw === "string" && /^[A-Z]{2}$/.test(raw) ? raw : null;
  res.setHeader("Cache-Control", "private, no-store");
  res.status(200).json({ country });
}
