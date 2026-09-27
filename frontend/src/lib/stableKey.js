/**
 * Mirror of ExamEntry.stable_key() in backend/models.py — MUST stay byte-for-byte
 * identical (backend/tests/test_units.py runs both and compares).
 *
 * sha1 of the normalised course code + date. For anything other than an exam
 * the kind and title are appended, exactly as the backend does, so an essay due
 * on the day of that course's exam gets its own key instead of colliding with
 * it — in the saved schedule, in the reminder queue and in Google Calendar.
 */
export async function stableKey(exam) {
  const code = (exam?.course_code || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const kind = exam?.kind || "exam";
  let basis = `${code}|${exam?.date || ""}`;
  if (kind !== "exam") {
    basis += `|${kind}|${(exam?.title || "").trim().toLowerCase()}`;
  }
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(basis));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
