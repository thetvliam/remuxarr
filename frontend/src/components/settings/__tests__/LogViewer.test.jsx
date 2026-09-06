/**
 * LogViewer — level emphasis and the level filter.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * LEVEL_ORDER deliberately ranks CRITICAL above ERROR, so that filtering on
 * ERROR includes CRITICAL. The row rendering did not follow: it bolded the
 * level label with `r.level === "ERROR" || r.level === "WARNING"`, naming two
 * levels instead of reading the rank, which left CRITICAL at normal weight.
 * Since buildLevelColor gives CRITICAL and ERROR the same red, the most
 * severe line in the buffer rendered less prominently than an ordinary error
 * directly above it, in an identical colour.
 *
 * CRITICAL is not something Remuxarr's own code emits — nothing calls
 * logger.critical — but the buffer is a handler on the ROOT logger at INFO,
 * so any dependency that emits one lands here.
 *
 * The filter itself is pinned alongside, because the bold rule and the filter
 * now read from the same table and a change to it moves both.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LogViewer } from "../LogViewer";
import { ThemeProvider } from "../../../theme";

const rec = (level, message) => ({
  ts: "2026-06-18 11:24:37.655", level, module: "worker", message,
});

const RECORDS = [
  rec("DEBUG",    "debug line"),
  rec("INFO",     "info line"),
  rec("WARNING",  "warning line"),
  rec("ERROR",    "error line"),
  rec("CRITICAL", "critical line"),
];

const renderViewer = (records = RECORDS) => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve({ records, total: records.length }) }),
  );
  return render(
    <ThemeProvider>
      <LogViewer api="" toast={vi.fn()} />
    </ThemeProvider>,
  );
};

/** The level label for a row. Scoped to the span: every level is also a
 *  filter button with the same text, so an unscoped lookup matches both. */
const weightOf = (level) =>
  screen.getByText(level, { selector: "span" }).style.fontWeight;

const clickFilter = (label) =>
  screen.getByRole("button", { name: label }).click();

beforeEach(() => { vi.restoreAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("LogViewer — level emphasis", () => {
  it("gives CRITICAL at least the emphasis of ERROR", async () => {
    renderViewer();
    await screen.findByText("critical line");

    expect(weightOf("CRITICAL")).toBe(weightOf("ERROR"));
  });

  it("emphasises WARNING and above, and nothing below it", async () => {
    renderViewer();
    await screen.findByText("info line");

    const bold = weightOf("ERROR");
    expect(weightOf("WARNING")).toBe(bold);
    expect(weightOf("CRITICAL")).toBe(bold);
    expect(weightOf("INFO")).not.toBe(bold);
  });

  it("leaves an unranked level unemphasised rather than defaulting it bold", async () => {
    // Anything the backend sends that is not in LEVEL_ORDER has no rank. It
    // must not fall on the emphasised side of the comparison.
    renderViewer([rec("ERROR", "error line"), rec("NOTICE", "odd line")]);
    await screen.findByText("error line");

    // Shown via ALL: an unranked level sorts below INFO, so the default
    // filter hides it entirely and there would be nothing to measure.
    clickFilter("ALL");
    await screen.findByText("odd line");

    expect(weightOf("NOTICE")).not.toBe(weightOf("ERROR"));
  });
});

describe("LogViewer — level filter", () => {
  it("shows CRITICAL when filtering on ERROR, since it outranks it", async () => {
    renderViewer();
    await screen.findByText("error line");

    clickFilter("ERROR");

    await waitFor(() => expect(screen.queryByText("warning line")).toBeNull());
    expect(screen.queryByText("critical line")).toBeTruthy();
    expect(screen.queryByText("error line")).toBeTruthy();
  });

  it("hides DEBUG at the default INFO filter", async () => {
    renderViewer();
    await screen.findByText("info line");

    expect(screen.queryByText("debug line")).toBeNull();
  });
});

/**
 * The poll loop.
 *
 * It refetches every 3s and wrote whatever came back straight into state,
 * with no status check and nothing tying a response to the request that asked
 * for it. Two things followed.
 *
 * An HTTP error parsed as data: `d.records || []` found no records on the
 * error body and emptied the view, so a single 500 blanked the log while the
 * backend was still running and the next tick refilled it. The network
 * failure path was already right — its catch leaves the lines alone — so the
 * two disagreed.
 *
 * And responses were applied in whatever order they arrived. At 3s intervals
 * against a slow backend two polls overlap, and the older one landing last
 * put stale lines back on screen.
 */
describe("LogViewer — polling", () => {
  let resolvers;

  const mockPoll = () => {
    resolvers = [];
    global.fetch = vi.fn(() => new Promise((res) => { resolvers.push(res); }));
  };
  const answer = (n, records) =>
    resolvers[n]({ ok: true, json: async () => ({ records, total: records.length }) });
  const failPoll = (n, status = 500) =>
    resolvers[n]({ ok: false, status, json: async () => ({ detail: "boom" }) });

  const renderBare = () => render(
    <ThemeProvider>
      <LogViewer api="" toast={vi.fn()} />
    </ThemeProvider>,
  );

  /** Let the next interval tick fire. */
  const tick = async () => {
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  };

  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); });

  it("keeps the lines on screen when a poll returns an HTTP error", async () => {
    mockPoll();
    renderBare();
    await act(async () => { answer(0, [rec("INFO", "first line")]); });
    await screen.findByText("first line");

    await tick();
    await act(async () => { failPoll(1); });

    expect(screen.getByText("first line")).toBeInTheDocument();
  });

  it("does not let a slow earlier poll overwrite a newer one", async () => {
    mockPoll();
    renderBare();
    await act(async () => { answer(0, [rec("INFO", "first line")]); });
    await screen.findByText("first line");

    await tick();
    await tick();

    await act(async () => { answer(2, [rec("INFO", "third line")]); });
    await screen.findByText("third line");
    await act(async () => { answer(1, [rec("INFO", "second line")]); });

    expect(screen.getByText("third line")).toBeInTheDocument();
    expect(screen.queryByText("second line")).not.toBeInTheDocument();
  });

  it("still applies each new poll response in order", async () => {
    // The guards must not freeze the view at whatever loaded first.
    mockPoll();
    renderBare();
    await act(async () => { answer(0, [rec("INFO", "first line")]); });
    await screen.findByText("first line");

    await tick();
    await act(async () => { answer(1, [rec("INFO", "second line")]); });

    await screen.findByText("second line");
    expect(screen.queryByText("first line")).not.toBeInTheDocument();
  });
});
