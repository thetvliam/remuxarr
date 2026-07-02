/* ═══════════════════════════════════════════════════════════════════════════
 * UTILITIES
 ═ ═*═════════════════════════════════════════════════════════════════════════ */

export const fmtSize = (bytes) => {
  if (!bytes) return "—";
  const b = parseInt(bytes);
  if (b < 1024)      return `${b} B`;
  if (b < 1024**2)   return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024**3)   return `${(b / 1024**2).toFixed(2)} MB`;
  return `${(b / 1024**3).toFixed(2)} GB`;
};

export const fmtDur = (secs) => {
  if (!secs) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return h ? `${h}h ${m}m` : `${m}m ${s}s`;
};

// SQLAlchemy returns UTC datetimes as "2026-06-18 11:24:37.655" — no
// timezone suffix.  JavaScript's Date() treats a space-separated datetime
// without 'Z' as LOCAL time, making everything appear offset by the
// browser's UTC offset (typically showing "1h ago" for something that just
// happened).  Normalise to an explicit UTC ISO string before parsing.
//
// NOTE: this function must not be altered or removed during refactoring —
// it is a deliberate bug fix, not incidental formatting logic.
export const toUtcDate = (iso) => {
  if (!iso) return null;
  // Already has timezone info — leave it alone
  if (iso.endsWith("Z") || iso.includes("+")) return new Date(iso);
  // "2026-06-18 11:24:37.123" → "2026-06-18T11:24:37.123Z"
  return new Date(iso.replace(" ", "T") + "Z");
};

export const fmtTime = (iso) => {
  const d = toUtcDate(iso);
  if (!d) return "—";
  return d.toLocaleTimeString("en-US", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
};

export const fmtRel = (iso) => {
  const d = toUtcDate(iso);
  if (!d) return "—";
  const mins = Math.floor((Date.now() - d) / 60000);
  if (mins < 1)  return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)  return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
};

export const basename = (path) => (path || "").split("/").pop() || path;

// Format a large count for compact display in a badge or tab.
// Keeps the tab width predictable without losing the order of magnitude.
//   999 → "999",  1000 → "1k",  19000 → "19k",  19500 → "19.5k"
export const fmtCount = (n) => {
  if (typeof n !== "number" || n < 1000) return String(n ?? "");
  const k = n / 1000;
  return (Number.isInteger(k) ? k.toFixed(0) : k.toFixed(1)) + "k";
};

// Shared by HistoryRow (compact inline text) and DetailModal (labelled Stat
// box) — both need the same underlying classification of a bytes_saved
// value, but render it differently. Centralising this here means the <1%
// guard and the positive/negative/zero split only exist in one place; if
// either display changes independently they could silently drift out of
// sync with each other.
//
// Returns null when there's no bytes_saved value at all (caller decides the
// appropriate "no data" fallback — HistoryRow shows "processed", DetailModal
// shows nothing).
export const formatBytesSaved = (bytesSaved, bytesSavedPct) => {
  if (bytesSaved == null) return null;
  const isPositive = bytesSaved > 0;
  const isNegative = bytesSaved < 0;
  return {
    isPositive,
    isNegative,
    isZero: !isPositive && !isNegative,
    sizeText: fmtSize(Math.abs(bytesSaved)),
    // Guard against showing "0%" when the saving is real but rounds to
    // less than 1% — without this, a small genuine saving misleadingly
    // displays as "(0%)".
    pctDisplay: (bytesSavedPct < 1 && bytesSavedPct > 0) ? "<1" : bytesSavedPct,
  };
};
