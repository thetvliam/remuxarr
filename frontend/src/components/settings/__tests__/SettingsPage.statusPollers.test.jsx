/**
 * SettingsPage — the two status pollers embedded in its groups.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * PlexBacklogStatus and EmailBreakerStatus poll on a 10s interval and wrote
 * whatever came back straight into state, with no status check and nothing
 * tying a response to the request that asked for it. Same shape as the
 * LogViewer poll, and the same two failures, but they show differently
 * because of what each component does with the value.
 *
 * Plex reads `d.count ?? 0`, so an error body became a count of zero: the
 * page went from "5 files queued" to "0 files queued" on a single 500, which
 * reads as work having drained rather than as a failed read. Before the first
 * successful poll it invented a zero where the component's own null means
 * "nothing known yet, render nothing".
 *
 * Email passes the body to setState unfiltered, so an error body has no
 * `tripped` and the warning banner vanished — the one case here where the
 * failure hides a problem rather than inventing one.
 *
 * Neither is exported, so both are driven through the page that mounts them,
 * which is also the only way a user meets them.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";

/* One field per group, because a group renders its poller only when it has
 * fields to render alongside. The group names are the real ones — they are
 * matched by string in renderGroup. */
const SCHEMA = [
  { key: "plex_analyze_enabled", label: "Plex analyze enabled", type: "boolean", group: "Plex Analyze Backlog" },
  { key: "email_enabled", label: "Email enabled", type: "boolean", group: "Email" },
];
const VALUES = { plex_analyze_enabled: true, email_enabled: true };

/** Resolvers for the polled endpoint, in the order the requests were made. */
let polls;

/**
 * Everything except the polled endpoint answers immediately; that one is left
 * pending so a test can choose when, and in what order, answers arrive.
 */
function mockApi(polledPath) {
  polls = [];
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = String(url);
    if (u.includes(polledPath)) {
      return new Promise((resolve) => { polls.push(resolve); });
    }
    if (u.includes("/api/settings/schema")) {
      return { ok: true, status: 200, json: async () => SCHEMA };
    }
    if (u.match(/\/api\/settings\/?$/)) {
      return { ok: true, status: 200, json: async () => VALUES };
    }
    return { ok: true, status: 200, json: async () => ({ value: null, count: 0 }) };
  }));
}

const answer = (n, body) =>
  polls[n]({ ok: true, status: 200, json: async () => body });
const failPoll = (n, status = 500) =>
  polls[n]({ ok: false, status, json: async () => ({ detail: "boom" }) });

async function renderCategory(category) {
  localStorage.setItem("remuxarr.settingsCategory", category);
  render(
    <ThemeProvider>
      <SettingsPage api="" toast={() => {}} />
    </ThemeProvider>,
  );
  // Wait for the schema/values load, so the group and its poller are mounted.
  await screen.findByText(/enabled/i);
}

/** Let the next 10s interval tick fire. */
const tick = async () => {
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
};

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
afterEach(() => { vi.useRealTimers(); });

describe("SettingsPage — Plex backlog poller", () => {
  const QUEUED = /queued for Plex re-analysis/;

  it("shows the count the server returned", async () => {
    mockApi("/api/plex/backlog");
    await renderCategory("integrations");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { count: 5 }); });

    expect(await screen.findByText(QUEUED)).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("renders nothing at all when the first poll errors", async () => {
    /* null means "nothing known yet" and renders nothing. Turning that into a
     * zero states a fact the server never gave. */
    mockApi("/api/plex/backlog");
    await renderCategory("integrations");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { failPoll(0); });

    expect(screen.queryByText(QUEUED)).not.toBeInTheDocument();
  });

  it("keeps the count it has when a later poll errors", async () => {
    mockApi("/api/plex/backlog");
    await renderCategory("integrations");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { count: 5 }); });
    await screen.findByText(QUEUED);

    await tick();
    await act(async () => { failPoll(1); });

    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("does not let a slow earlier poll overwrite a newer one", async () => {
    mockApi("/api/plex/backlog");
    await renderCategory("integrations");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { count: 5 }); });
    await screen.findByText(QUEUED);

    await tick();
    await tick();
    await act(async () => { answer(2, { count: 7 }); });
    await screen.findByText("7");
    await act(async () => { answer(1, { count: 99 }); });

    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.queryByText("99")).not.toBeInTheDocument();
  });

  it("still applies each new poll response in order", async () => {
    // The guards must not freeze the badge at whatever loaded first.
    mockApi("/api/plex/backlog");
    await renderCategory("integrations");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { count: 5 }); });
    await screen.findByText(QUEUED);

    await tick();
    await act(async () => { answer(1, { count: 7 }); });

    expect(await screen.findByText("7")).toBeInTheDocument();
  });
});

describe("SettingsPage — email breaker poller", () => {
  const PAUSED = /Failure notifications are paused/;

  it("shows the banner while the breaker is tripped", async () => {
    mockApi("/api/notifications/state");
    await renderCategory("notifications");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { tripped: true, consecutive_failures: 3 }); });

    expect(await screen.findByText(PAUSED)).toBeInTheDocument();
  });

  it("does not dismiss the banner because a poll errored", async () => {
    /* The breaker is still tripped; only the read failed. Clearing the
     * warning says the opposite of what is true. */
    mockApi("/api/notifications/state");
    await renderCategory("notifications");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { tripped: true, consecutive_failures: 3 }); });
    await screen.findByText(PAUSED);

    await tick();
    await act(async () => { failPoll(1); });

    expect(screen.getByText(PAUSED)).toBeInTheDocument();
  });

  it("does not let a slow earlier poll overwrite a newer one", async () => {
    /* The banner is the wrong thing to resurrect from a stale read: it tells
     * the user email is currently paused. */
    mockApi("/api/notifications/state");
    await renderCategory("notifications");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { tripped: true, consecutive_failures: 3 }); });
    await screen.findByText(PAUSED);

    await tick();
    await tick();
    await act(async () => { answer(2, { tripped: false }); });
    await waitFor(() => expect(screen.queryByText(PAUSED)).not.toBeInTheDocument());
    await act(async () => { answer(1, { tripped: true, consecutive_failures: 9 }); });

    expect(screen.queryByText(PAUSED)).not.toBeInTheDocument();
  });

  it("still clears the banner when the breaker resets", async () => {
    // A real untripped answer must still land, or the banner would be stuck.
    mockApi("/api/notifications/state");
    await renderCategory("notifications");
    await waitFor(() => expect(polls.length).toBeGreaterThan(0));
    await act(async () => { answer(0, { tripped: true, consecutive_failures: 3 }); });
    await screen.findByText(PAUSED);

    await tick();
    await act(async () => { answer(1, { tripped: false }); });

    await waitFor(() => expect(screen.queryByText(PAUSED)).not.toBeInTheDocument());
  });
});
