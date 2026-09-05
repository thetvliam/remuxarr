/**
 * utils.js — the shared formatters.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Thirteen modules import from here and nothing tested it directly. Most of
 * what is in there is not formatting for its own sake: the null-versus-zero
 * split in fmtSize and fmtDur, the UTC normalisation in toUtcDate, and the
 * sub-1% rule in formatBytesSaved are each written up in the source as a bug
 * that was found and fixed. Those comments were the only thing holding the
 * behaviour in place, and a comment does not fail when someone simplifies the
 * line under it.
 *
 * So these pin the decisions, not the digits. Where a case is documented as
 * deliberate, the test says which reading it is ruling out.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  basename, fmtClock, fmtCount, fmtDur, fmtRel, fmtSize, fmtTime, formatBytesSaved,
  toUtcDate,
} from "../utils";

/* ── Timezone ──────────────────────────────────────────────────────────────
 *
 * The point of toUtcDate is that a timestamp with no zone is read as UTC
 * rather than as local time. On a UTC host both readings agree, so a test
 * asserting it there passes whether or not the conversion happens at all —
 * the same trap tests/test_timestamp_roundtrip.py records on the backend,
 * where the container and the CI runner are both UTC.
 *
 * So the zone is forced, and the offset is asserted before anything depends
 * on it. Without that check a fixture that silently stopped working would
 * leave every assertion below passing for the wrong reason. */
const ORIGINAL_TZ = process.env.TZ;
beforeAll(() => { process.env.TZ = "America/New_York"; });
afterAll(() => { process.env.TZ = ORIGINAL_TZ; });

describe("the timezone fixture itself", () => {
  it("puts the host somewhere that is not UTC", () => {
    expect(Math.abs(new Date().getTimezoneOffset())).toBeGreaterThan(60);
  });
});

/* ── fmtSize ─────────────────────────────────────────────────────────────── */

describe("fmtSize", () => {
  it("tells a zero size apart from a missing one", () => {
    /* The documented decision. Both used to read as the em dash, which hid
     * the difference between a remux that saved nothing and one whose saving
     * was never recorded. */
    expect(fmtSize(0)).toBe("0 B");
    expect(fmtSize(null)).toBe("—");
    expect(fmtSize(undefined)).toBe("—");
  });

  it("shows a value that is not a number as unknown rather than NaN", () => {
    expect(fmtSize("not a number")).toBe("—");
  });

  it("changes unit at each 1024 boundary", () => {
    expect(fmtSize(1023)).toBe("1023 B");
    expect(fmtSize(1024)).toBe("1.0 KB");
    expect(fmtSize(1024 ** 2 - 1)).toBe("1024.0 KB");
    expect(fmtSize(1024 ** 2)).toBe("1.00 MB");
    expect(fmtSize(1024 ** 3)).toBe("1.00 GB");
  });
});

/* ── fmtDur ──────────────────────────────────────────────────────────────── */

describe("fmtDur", () => {
  it("tells a zero duration apart from a missing one", () => {
    expect(fmtDur(0)).toBe("0m 0s");
    expect(fmtDur(null)).toBe("—");
    expect(fmtDur("not a number")).toBe("—");
  });

  it("drops seconds once there is an hour to show", () => {
    // Under an hour the seconds matter; over it they are noise next to the
    // hours, so the shape of the string changes rather than growing.
    expect(fmtDur(59)).toBe("0m 59s");
    expect(fmtDur(3599)).toBe("59m 59s");
    expect(fmtDur(3600)).toBe("1h 0m");
    expect(fmtDur(3661)).toBe("1h 1m");
  });
});

/* ── toUtcDate, through the two formatters that use it ───────────────────── */

describe("fmtTime and fmtClock", () => {
  /** What the browser shows for an instant, given the forced zone. */
  const localised = (utcMs, withSeconds) =>
    new Date(utcMs).toLocaleTimeString("en-US", {
      hour: "2-digit", minute: "2-digit",
      ...(withSeconds ? { second: "2-digit" } : {}),
      hour12: false,
    });

  const INSTANT = Date.UTC(2026, 5, 18, 11, 24, 37, 655);

  it("reads a timestamp with no zone as UTC, not as local time", () => {
    /* The bug the function exists for. Without it JavaScript takes the
     * space-separated form as local, and everything renders shifted by the
     * viewer's offset — "1h ago" for something that just happened. */
    expect(fmtTime("2026-06-18 11:24:37.655")).toBe(localised(INSTANT, false));
  });

  it("does not render it as the local reading of the same digits", () => {
    // The assertion above passes on a UTC host either way. This one is what
    // distinguishes the two readings, and it needs the forced zone.
    const asLocal = new Date("2026-06-18T11:24:37.655").toLocaleTimeString(
      "en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
    expect(fmtTime("2026-06-18 11:24:37.655")).not.toBe(asLocal);
  });

  it("leaves a timestamp that already carries a zone alone", () => {
    expect(fmtTime("2026-06-18T11:24:37.655Z")).toBe(localised(INSTANT, false));
  });

  it("shows seconds, which is the whole reason the log viewer uses it", () => {
    // Ordering events inside a minute is what a log timestamp is for, so
    // fmtClock exists purely to carry the seconds fmtTime drops.
    expect(fmtClock("2026-06-18 11:24:37.655")).toBe(localised(INSTANT, true));
    expect(fmtClock("2026-06-18 11:24:37.655")).not.toBe(fmtTime("2026-06-18 11:24:37.655"));
  });

  it("shows nothing rather than an invalid date when there is no timestamp", () => {
    expect(fmtTime(null)).toBe("—");
    expect(fmtClock("")).toBe("—");
  });
});

/* ── toUtcDate, directly ─────────────────────────────────────────────────── */

describe("toUtcDate", () => {
  /* Tested here as well as through the formatters above, because it has a
   * branch none of them reach: a string that already declares an offset. The
   * formatters only ever see what the backend sends, which is naive UTC.
   *
   * These compare instants rather than rendered strings, so unlike the
   * formatter tests they do not depend on the forced zone. */
  const INSTANT = Date.UTC(2026, 5, 18, 11, 24, 37, 655);

  it("reads a timestamp with no zone as UTC", () => {
    expect(toUtcDate("2026-06-18 11:24:37.655").getTime()).toBe(INSTANT);
  });

  it("leaves a timestamp that already declares Z alone", () => {
    expect(toUtcDate("2026-06-18T11:24:37.655Z").getTime()).toBe(INSTANT);
  });

  it("leaves a timestamp that already declares an offset alone", () => {
    /* Appending Z to this would produce an Invalid Date rather than a wrong
     * time, so the failure is loud — but only if something reaches the
     * branch, and nothing did before this test. */
    expect(toUtcDate("2026-06-18T13:24:37.655+02:00").getTime()).toBe(INSTANT);
  });

  /* Not tested: a NEGATIVE offset, e.g. "2026-06-18T06:24:37.655-05:00".
   * It matches neither guard, so it has "Z" appended and comes back as an
   * Invalid Date. Left alone rather than pinned, because asserting the
   * current result would lock the fault in, and the source says this
   * function must not be altered. Unreachable from this backend, which sends
   * naive UTC — reachable only if a caller ever passes a timestamp from
   * somewhere else. */

  it("returns nothing when there is no timestamp to convert", () => {
    expect(toUtcDate(null)).toBeNull();
    expect(toUtcDate("")).toBeNull();
  });
});

/* ── fmtRel ──────────────────────────────────────────────────────────────── */

describe("fmtRel", () => {
  const NOW = Date.UTC(2026, 5, 18, 12, 0, 0);
  /** A naive UTC timestamp `mins` before NOW, in the shape the backend sends. */
  const agoBy = (mins) =>
    new Date(NOW - mins * 60000).toISOString().replace("T", " ").replace("Z", "");

  beforeAll(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
  afterAll(() => { vi.useRealTimers(); });

  it("steps up a unit at each boundary", () => {
    expect(fmtRel(agoBy(0))).toBe("just now");
    expect(fmtRel(agoBy(1))).toBe("1m ago");
    expect(fmtRel(agoBy(59))).toBe("59m ago");
    expect(fmtRel(agoBy(60))).toBe("1h ago");
    expect(fmtRel(agoBy(60 * 24 - 1))).toBe("23h ago");
    expect(fmtRel(agoBy(60 * 24))).toBe("1d ago");
  });

  it("reads the timestamp as UTC like the other formatters", () => {
    /* If it took the naive string as local, everything on this host would
     * come back hours out — which is exactly how the bug presented. */
    expect(fmtRel(agoBy(5))).toBe("5m ago");
  });

  it("shows nothing rather than a relative time when there is no timestamp", () => {
    expect(fmtRel(null)).toBe("—");
  });
});

/* ── basename ────────────────────────────────────────────────────────────── */

describe("basename", () => {
  it("takes the last segment of a path", () => {
    expect(basename("/media/tv/show/ep.mkv")).toBe("ep.mkv");
    expect(basename("ep.mkv")).toBe("ep.mkv");
  });

  it("falls back to the whole input when there is no last segment", () => {
    /* The trailing `|| path` is what does this, and it is why a nullish input
     * comes back out unchanged instead of as the empty string the leading
     * `(path || "")` suggests. Pinned as current behaviour rather than
     * endorsed: callers all render it through a `|| "—"` of their own, so
     * nothing depends on which of the two it is today. */
    expect(basename("/media/tv/")).toBe("/media/tv/");
    expect(basename(null)).toBeNull();
    expect(basename("")).toBe("");
  });
});

/* ── fmtCount ────────────────────────────────────────────────────────────── */

describe("fmtCount", () => {
  it("leaves anything under a thousand as it is", () => {
    expect(fmtCount(0)).toBe("0");
    expect(fmtCount(999)).toBe("999");
  });

  it("abbreviates from a thousand up, keeping a tenth only when it says something", () => {
    // A fixed width is the point — a tab badge cannot grow with the count.
    expect(fmtCount(1000)).toBe("1k");
    expect(fmtCount(19000)).toBe("19k");
    expect(fmtCount(19500)).toBe("19.5k");
  });

  it("renders a missing count as empty rather than as the word null", () => {
    expect(fmtCount(null)).toBe("");
    expect(fmtCount(undefined)).toBe("");
  });
});

/* ── formatBytesSaved ────────────────────────────────────────────────────── */

describe("formatBytesSaved", () => {
  it("returns nothing at all when there is no saving recorded", () => {
    // Distinct from a saving of zero: the caller picks its own fallback.
    expect(formatBytesSaved(null, null)).toBeNull();
  });

  it("classifies a saving as positive, negative or zero", () => {
    expect(formatBytesSaved(1024, 5).isPositive).toBe(true);
    expect(formatBytesSaved(-1024, -5).isNegative).toBe(true);
    expect(formatBytesSaved(0, 0).isZero).toBe(true);
  });

  it("reports the size of a negative saving without its sign", () => {
    // The direction is carried by isNegative; the caller draws it.
    expect(formatBytesSaved(-2048, -5).sizeText).toBe("2.0 KB");
  });

  it("shows a real saving too small to round as <1 rather than 0%", () => {
    /* The documented fix, and the reason the trigger is the byte delta
     * rather than the percentage. bytesSavedPct arrives already rounded to
     * one decimal, so 5 KB saved on a 300 MB file reaches here as exactly 0.
     * Keying off the percentage let those through as "0%", which reads as
     * having saved nothing when bytes really were saved. */
    expect(formatBytesSaved(5120, 0).pctDisplay).toBe("<1");
    expect(formatBytesSaved(5120, 0.4).pctDisplay).toBe("<1");
  });

  it("leaves a percentage of 1 or more alone", () => {
    expect(formatBytesSaved(1024 ** 2, 1).pctDisplay).toBe(1);
    expect(formatBytesSaved(1024 ** 2, 12.5).pctDisplay).toBe(12.5);
  });

  it("does not apply the <1 rule to a file that grew", () => {
    // "<1" would claim a saving. A negative delta is reported as its own
    // percentage however small.
    expect(formatBytesSaved(-5120, -0.4).pctDisplay).toBe(-0.4);
  });

  it("treats a missing percentage on a real saving as <1", () => {
    // Null coerces to 0 and so takes the branch above, which is the reading
    // the source records as intended.
    expect(formatBytesSaved(5120, null).pctDisplay).toBe("<1");
  });
});

afterEach(() => { vi.restoreAllMocks(); });
