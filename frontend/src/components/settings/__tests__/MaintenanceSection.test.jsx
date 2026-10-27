/**
 * MaintenanceSection — Orphaned Files removal.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The same failure DangerZone.test.jsx was written for, in the section
 * directly above it. Removing orphaned entries runs the backend's
 * _delete_media_file_and_related, which deletes the queue items and their
 * planned actions, the forge jobs, the Plex backlog and both language-flag
 * tables for every selected file, and detaches its revert points. Manual
 * Cleanup, which invalidates strictly less, broadcasts cleanup_completed and
 * gets every panel refreshed for free. POST /api/scan/orphaned/remove
 * broadcasts nothing, and this section is reached from Settings with the
 * dashboard unmounted, so its state sits untouched in useAppData: going back
 * showed a queue and a history still listing rows the removal had deleted,
 * a review page still counting flags that were gone, and a recycle bin still
 * calling detached points attached.
 *
 * The component took { api, toast, reloadKey } and had no way to say
 * otherwise. The rest of the file covers the arming and the request itself,
 * since a removal fired on one click is not recoverable from the UI.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MaintenanceSection } from "../MaintenanceSection";
import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";
import { CONFIRM_MS } from "../../../constants";
import { stubViewport } from "../../../test/viewport";

const ORPHAN = {
  id: 7, filename: "movie.mkv", path: "/old/movie.mkv", on_disk: false,
};

/**
 * The three settings reads on mount, then whatever the test needs. Routed by
 * URL rather than by call order: the mount reads are a Promise.all, so their
 * order is not something a test should depend on.
 */
const mockApi = ({ orphaned = [ORPHAN], removeOk = true,
                   checkOk = true, detail = "nope" } = {}) => {
  const removeCalls = [];
  vi.stubGlobal("fetch", vi.fn(async (url, opts) => {
    const u = String(url);
    if (u.includes("/api/settings/scheduled_scan_enabled"))
      return { ok: true, json: async () => ({ value: false }) };
    if (u.includes("/api/settings/scheduled_scan_times"))
      return { ok: true, json: async () => ({ value: [] }) };
    if (u.includes("/api/settings/auto_cleanup_on_scan"))
      return { ok: true, json: async () => ({ value: true }) };
    if (u.includes("/api/scan/orphaned/remove")) {
      removeCalls.push(JSON.parse(opts.body));
      return removeOk
        ? { ok: true, json: async () => ({ removed: orphaned.length }) }
        : { ok: false, json: async () => (detail ? { detail } : {}) };
    }
    if (u.includes("/api/scan/orphaned"))
      return checkOk
        ? { ok: true, json: async () => ({ total: orphaned.length, items: orphaned }) }
        : { ok: false, json: async () => (detail ? { detail } : {}) };
    return { ok: true, json: async () => ({}) };
  }));
  return { removeCalls };
};

const setup = (props = {}) => {
  const onRecordsRemoved = vi.fn();
  const toast = vi.fn();
  render(
    <ThemeProvider>
      <MaintenanceSection api="" toast={toast}
                          onRecordsRemoved={onRecordsRemoved} {...props} />
    </ThemeProvider>,
  );
  return { onRecordsRemoved, toast };
};

const removeButton = () => screen.getByRole("button", { name: /REMOVE SELECTED|CONFIRM|REMOVING/i });

/** Check for orphans, select the row, and click through the confirmation. */
const removeOrphans = async (user) => {
  await user.click(screen.getByRole("button", { name: /CHECK FOR ORPHANED FILES/i }));
  await screen.findByText(ORPHAN.filename);
  // Click the row, not a checkbox by index: the card also renders the two
  // scheduling toggles and a select-all, so an index would silently move.
  await user.click(screen.getByText(ORPHAN.filename));
  await user.click(removeButton());
  await user.click(removeButton());
};

describe("MaintenanceSection — orphaned removal", () => {
  beforeEach(() => { vi.unstubAllGlobals(); });

  it("tells the app to refresh once entries are removed", async () => {
    mockApi();
    const user = userEvent.setup();
    const { onRecordsRemoved } = setup();

    await removeOrphans(user);

    await waitFor(() => expect(onRecordsRemoved).toHaveBeenCalledTimes(1));
  });

  it("does not refresh when the removal fails", async () => {
    /** A failed request deleted nothing, so there is nothing to refresh —
     *  and a refresh here would paper over the failure by making the panels
     *  look freshly reloaded. */
    mockApi({ removeOk: false });
    const user = userEvent.setup();
    const { onRecordsRemoved, toast } = setup();

    await removeOrphans(user);

    await waitFor(() => expect(toast).toHaveBeenCalledWith("nope", "error"));
    expect(onRecordsRemoved).not.toHaveBeenCalled();
  });

  it("shows why the server refused rather than a generic failure", async () => {
    /** The server declines both orphan routes when no scan paths are
     *  configured, because with none every row in the library counts as
     *  orphaned. Its reason names Clear Database as the action that does
     *  mean "remove everything" — replacing that with "Failed to remove
     *  orphaned files" leaves someone with no idea what to do instead, and
     *  the obvious next move is to add a throwaway scan path and come
     *  straight back to this button. */
    const reason = "No scan paths configured, so every file would be " +
      "reported as orphaned. Add a library path first, or use Clear " +
      "Database if you mean to remove everything.";
    mockApi({ removeOk: false, detail: reason });
    const user = userEvent.setup();
    const { toast } = setup();

    await removeOrphans(user);

    await waitFor(() => expect(toast).toHaveBeenCalledWith(reason, "error"));
  });

  it("falls back to its own message when the server gives no reason", async () => {
    /** A 502 from a reverse proxy has no JSON body at all. Surfacing the
     *  server's reason must not mean showing nothing when there is none. */
    mockApi({ removeOk: false, detail: null });
    const user = userEvent.setup();
    const { toast } = setup();

    await removeOrphans(user);

    await waitFor(() => expect(toast).toHaveBeenCalledWith(
      "Failed to remove orphaned files", "error"));
  });

  it("shows why the check was refused", async () => {
    /** The same refusal on the listing, which is the one most people hit
     *  first — the button that fetches the list is what they press. */
    const reason = "No scan paths configured, so every file would be " +
      "reported as orphaned.";
    mockApi({ checkOk: false, detail: reason });
    const user = userEvent.setup();
    const { toast } = setup();

    await user.click(await screen.findByRole("button",
      { name: /check for orphaned/i }));

    await waitFor(() => expect(toast).toHaveBeenCalledWith(reason, "error"));
  });

  it("sends the selected ids and reports what was removed", async () => {
    const { removeCalls } = mockApi();
    const user = userEvent.setup();
    const { toast } = setup();

    await removeOrphans(user);

    await waitFor(() => expect(removeCalls).toHaveLength(1));
    expect(removeCalls[0]).toEqual({ file_ids: [ORPHAN.id] });
    expect(toast).toHaveBeenCalledWith("Removed 1 orphaned entry", "info");
  });

  it("requires a second click before anything is removed", async () => {
    const { removeCalls } = mockApi();
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByRole("button", { name: /CHECK FOR ORPHANED FILES/i }));
    await screen.findByText(ORPHAN.filename);
    await user.click(screen.getByText(ORPHAN.filename));
    await user.click(removeButton());

    expect(removeButton()).toHaveTextContent(/CONFIRM/i);
    expect(removeCalls).toHaveLength(0);
  });

  it("disarms itself once the confirmation window lapses", async () => {
    /* Read from the shared constant, not written as a number: this site's own
       comment claimed 3s while using CONFIRM_MS, which is the drift the
       constant exists to stop. A literal here would let it drift again while
       still passing.

       fireEvent rather than userEvent, matching DangerZone.test.jsx —
       userEvent schedules its own work on the timers this replaces, and the
       two deadlock. */
    const { removeCalls } = mockApi();
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByRole("button", { name: /CHECK FOR ORPHANED FILES/i }));
    await screen.findByText(ORPHAN.filename);
    await user.click(screen.getByText(ORPHAN.filename));

    vi.useFakeTimers();
    try {
      fireEvent.click(removeButton());
      expect(removeButton()).toHaveTextContent(/CONFIRM/i);

      act(() => { vi.advanceTimersByTime(CONFIRM_MS - 100); });
      expect(removeButton()).toHaveTextContent(/CONFIRM/i);

      act(() => { vi.advanceTimersByTime(200); });
      expect(removeButton()).not.toHaveTextContent(/CONFIRM/i);
    } finally {
      vi.useRealTimers();
    }
    expect(removeCalls).toHaveLength(0);
  });

  it("disarms the force rescan once the confirmation window lapses", async () => {
    /* The second armed button in this component, and the one that had no
     * test: deleting its timeout outright left all 466 tests passing. Force
     * Full Rescan walks the whole library, so a button left armed
     * indefinitely is one stray click from a full rescan the user has
     * forgotten they started.
     *
     * Same shape as the orphaned-removal test above — the constant rather
     * than a literal, and fireEvent rather than userEvent, which schedules
     * its own work on the timers this replaces and deadlocks with them. */
    mockApi();
    setup();
    // Let the settings reads on mount land first: under the fake timers
    // below they would arrive after the test, outside act().
    await act(async () => {});

    const btn = () => screen.getByRole("button", { name: /FORCE FULL RESCAN|CLICK AGAIN TO CONFIRM/i });

    vi.useFakeTimers();
    try {
      fireEvent.click(btn());
      expect(btn()).toHaveTextContent(/CONFIRM/i);

      act(() => { vi.advanceTimersByTime(CONFIRM_MS - 100); });
      expect(btn()).toHaveTextContent(/CONFIRM/i);

      act(() => { vi.advanceTimersByTime(200); });
      expect(btn()).not.toHaveTextContent(/CONFIRM/i);
    } finally {
      vi.useRealTimers();
    }
  });
});


/**
 * Placed here rather than with the SettingsPage tests because the subject is
 * the same one this file exists for: whether a removal actually reaches the
 * dashboard. The tests above prove MaintenanceSection calls its callback;
 * none of them would notice SettingsPage forgetting to pass it down, which
 * puts the app back exactly where it started with every unit test green.
 */
describe("MaintenanceSection — wiring through SettingsPage", () => {
  beforeEach(() => { vi.unstubAllGlobals(); });

  it("hands an orphaned removal back to the page's onRecordsRemoved", async () => {
    const inner = mockApi();
    const realFetch = globalThis.fetch;
    // Layer the page's own two loads over the maintenance endpoints above.
    vi.stubGlobal("fetch", vi.fn(async (url, opts) => {
      const u = String(url);
      if (u.includes("/api/settings/schema"))
        return { ok: true, json: async () => [] };
      if (u.match(/\/api\/settings\/?$/))
        return { ok: true, json: async () => ({}) };
      return realFetch(url, opts);
    }));

    const onRecordsRemoved = vi.fn();
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <SettingsPage api="" toast={vi.fn()} onDirtyChange={() => {}}
                      onRecordsRemoved={onRecordsRemoved} />
      </ThemeProvider>,
    );

    await user.click(await screen.findByText("Maintenance & Logs"));
    await removeOrphans(user);

    await waitFor(() => expect(onRecordsRemoved).toHaveBeenCalledTimes(1));
    expect(inner.removeCalls).toEqual([{ file_ids: [ORPHAN.id] }]);
  });

  it("hands a database clear back to the same callback", async () => {
    /**
     * The other half of the prop. It was onDatabaseCleared, reaching only
     * DangerZone; sharing it with the orphaned removal meant renaming it, and
     * nothing anywhere asserted that SettingsPage passed it to DangerZone at
     * all — DangerZone.test.jsx supplies onCleared itself. Deleting the prop
     * from that line left the whole suite green, so the rename could have
     * quietly unhooked Clear Database and no test would have said so.
     */
    const clears = [];
    vi.stubGlobal("fetch", vi.fn(async (url, opts) => {
      const u = String(url);
      if (u.includes("/api/settings/clear-database")) {
        clears.push(u);
        return { ok: true, json: async () => ({}) };
      }
      if (u.includes("/api/settings/schema"))
        return { ok: true, json: async () => [] };
      if (u.match(/\/api\/settings\/?$/))
        return { ok: true, json: async () => ({}) };
      return { ok: true, json: async () => ({ value: null, count: 0 }) };
    }));

    const onRecordsRemoved = vi.fn();
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <SettingsPage api="" toast={vi.fn()} onDirtyChange={() => {}}
                      onRecordsRemoved={onRecordsRemoved} />
      </ThemeProvider>,
    );

    await user.click(await screen.findByText("Backup & Danger Zone"));
    const clear = () => screen.getByRole("button", { name: /CLEAR DATABASE|CONFIRM|CLEARING/i });
    await user.click(clear());
    await user.click(clear());

    await waitFor(() => expect(clears).toHaveLength(1));
    await waitFor(() => expect(onRecordsRemoved).toHaveBeenCalledTimes(1));
  });
});

/**
 * The settings load, and what a failed reload does to it.
 *
 * The three reads on mount parsed without checking the status, so an error
 * body reached them all: `!!enabled.value` found undefined and gave false,
 * the times gave [], and `cleanup.value !== false` gave true.
 *
 * On first mount that is invisible, because those three happen to be exactly
 * the initial state. It bites on the reload. SettingsPage bumps reloadKey
 * after a settings import, and if that reload errors the panel silently
 * replaces whatever was loaded with the defaults — scheduled scans off, no
 * scan times, auto-cleanup on — while the server has something else. The
 * network-failure path was already right, its catch leaves the state alone,
 * so the two disagreed only because one of them was never checked.
 *
 * Note the three fallbacks are NOT interchangeable and the difference is not
 * a mistake: each matches its own key's server default in session.py, which
 * is False for scheduled_scan_enabled, [] for scheduled_scan_times, and True
 * for auto_cleanup_on_scan. Normalising them to one form would put a wrong
 * default on two of the three.
 */
describe("MaintenanceSection — settings load", () => {
  /* The three settings reads are left pending so a test can resolve them at a
   * known point. The alternative, an immediately-resolving mock plus waitFor,
   * races here: the assertion for "nothing changed" passes against the state
   * from the PREVIOUS load before the reload has landed, so the test reports
   * success without ever having exercised the reload. */
  let pending;

  const mockSettings = () => {
    pending = [];
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const u = String(url);
      const key = ["scheduled_scan_enabled", "scheduled_scan_times", "auto_cleanup_on_scan"]
        .find(k => u.includes(`/api/settings/${k}`));
      if (key) return new Promise((resolve) => { pending.push({ key, resolve }); });
      if (u.includes("/api/scan/orphaned"))
        return { ok: true, json: async () => ({ total: 0, items: [] }) };
      return { ok: true, json: async () => ({}) };
    }));
  };

  /** Answer the three reads of one load, either with values or with errors. */
  const settle = async ({ enabled, times, cleanup, ok = true }) => {
    await waitFor(() => expect(pending).toHaveLength(3));
    const batch = pending.splice(0, 3);
    const value = { scheduled_scan_enabled: enabled, scheduled_scan_times: times,
                    auto_cleanup_on_scan: cleanup };
    await act(async () => {
      for (const { key, resolve } of batch) {
        resolve(ok
          ? { ok: true, status: 200, json: async () => ({ value: value[key] }) }
          : { ok: false, status: 500, json: async () => ({ detail: "boom" }) });
      }
    });
  };

  const renderAt = (reloadKey) => render(
    <ThemeProvider>
      <MaintenanceSection api="" toast={vi.fn()} reloadKey={reloadKey} />
    </ThemeProvider>,
  );
  const reloadTo = (rerender, reloadKey) => rerender(
    <ThemeProvider>
      <MaintenanceSection api="" toast={vi.fn()} reloadKey={reloadKey} />
    </ThemeProvider>,
  );

  const scans   = () => screen.getByRole("switch", { name: "Enable Scheduled Scans" });
  const cleanup = () => screen.getByRole("switch", { name: "Auto-cleanup on Scan" });

  /* Non-default on every key, so a reset to defaults shows on all three
   * rather than only on the one whose default differs. */
  const LOADED = { enabled: true, times: ["03:00"], cleanup: false };

  it("shows what the server returned", async () => {
    mockSettings();
    renderAt(0);
    await settle(LOADED);

    expect(scans()).toHaveAttribute("aria-checked", "true");
    expect(cleanup()).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("03:00")).toBeInTheDocument();
  });

  it("keeps the loaded values when a reload returns an HTTP error", async () => {
    mockSettings();
    const { rerender } = renderAt(0);
    await settle(LOADED);

    reloadTo(rerender, 1);
    await settle({ ok: false });

    // Each of the three would flip to its own default if the error body were
    // read as data: scans off, times empty, auto-cleanup on.
    expect(scans()).toHaveAttribute("aria-checked", "true");
    expect(cleanup()).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("03:00")).toBeInTheDocument();
  });

  it("falls back to each key's own server default when a value is null", async () => {
    /* The PUT body is typed Any, so a stored null comes back on a 200 and
     * every fallback is exercised at once. They are not interchangeable:
     * session.py defaults scheduled_scan_enabled False, scheduled_scan_times
     * [] and auto_cleanup_on_scan True, so the right answer here is off,
     * empty and ON. Writing all three the same way would get two of them
     * wrong, and this is what says so. */
    mockSettings();
    renderAt(0);
    await settle({ enabled: null, times: null, cleanup: null });

    expect(scans()).toHaveAttribute("aria-checked", "false");
    expect(cleanup()).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByText("03:00")).not.toBeInTheDocument();
  });

  it("ignores scan times that come back as something other than a list", async () => {
    /* Reachable through the settings import, whose body is unvalidated. The
     * check has to be on the type, not on truthiness: TimeTagInput maps over
     * this, and a bare string is truthy and would reach it. */
    mockSettings();
    renderAt(0);
    await settle({ enabled: false, times: "03:00", cleanup: true });

    expect(screen.queryByText("03:00")).not.toBeInTheDocument();
  });

  it("still applies the new values when a reload succeeds", async () => {
    // The guard must not freeze the panel at whatever loaded first.
    mockSettings();
    const { rerender } = renderAt(0);
    await settle(LOADED);

    reloadTo(rerender, 1);
    await settle({ enabled: false, times: ["09:15"], cleanup: true });

    expect(scans()).toHaveAttribute("aria-checked", "false");
    expect(cleanup()).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("09:15")).toBeInTheDocument();
  });
});

describe("MaintenanceSection — on a phone and on a desktop", () => {
  /* Every row here is an ActionRow, the two switches and the three cards'
   * buttons alike: on a phone the control goes below its description, on a
   * desktop beside it. The cards' rows used to wrap only when the button no
   * longer fitted beside a 220px description, and this pinned that wrap; the
   * switch rows never wrapped at all. jsdom has no layout, so these pin the
   * direction each row asks for, found from its control. */
  it.each([
    ["switch", "Enable Scheduled Scans"],
    ["switch", "Auto-cleanup on Scan"],
    ["button", "RUN CLEANUP NOW"],
    ["button", "FORCE FULL RESCAN"],
    ["button", "CHECK FOR ORPHANED FILES"],
  ])("puts the %s %s below its description on a phone, beside it on a desktop", async (role, name) => {
    for (const [width, direction] of [[390, "column"], [1280, "row"]]) {
      stubViewport(width);
      mockApi();
      setup();
      await act(async () => {});  // let the settings reads on mount land

      expect([width, screen.getByRole(role, { name }).parentElement.style.flexDirection])
        .toEqual([width, direction]);
      cleanup();
    }
  });
});

describe("MaintenanceSection — its switches", () => {
  /* Both switches save the moment they are flipped, through PUT
   * /api/settings/{key}; there is no Save button for them. They are the
   * page's shared OnOffSwitch now, where they were sliding switches of their
   * own, and nothing tested that flipping one saved anything. The mock loads
   * scheduled scans off and auto-cleanup on, so each has to send the
   * opposite of what it loaded. Verified by mutation: Enable Scheduled Scans
   * no longer wired to saving survived the suite before these and is
   * killed by them. */
  it.each([
    ["Enable Scheduled Scans", "scheduled_scan_enabled", true],
    ["Auto-cleanup on Scan", "auto_cleanup_on_scan", false],
  ])("saves %s as the opposite of what it loaded when clicked", async (name, key, sent) => {
    mockApi();
    setup();
    await act(async () => {});  // let the settings reads on mount land
    const sw = screen.getByRole("switch", { name });

    await act(async () => { fireEvent.click(sw); });

    const puts = fetch.mock.calls.filter(([, opts]) => opts?.method === "PUT");
    expect(puts.map(([url, opts]) => [String(url), JSON.parse(opts.body)]))
      .toEqual([[`/api/settings/${key}`, { value: sent }]]);
    expect(sw).toHaveAttribute("aria-checked", String(sent));
  });
});


/**
 * Re-check Scanned Files decides every file already scanned again against
 * the current settings, from its stored tracks, so a settings change reaches
 * files a routine scan skips. It can queue a job for each one, so it takes
 * two clicks, like Force Full Rescan beside it.
 *
 * Verified by mutation, each run against the whole suite before these tests
 * existed and surviving it: the shared startRecheck sending force_probe
 * rather than recheck (also killed by the save-bar tests, which share it),
 * the confirm step removed, and the card not calling startRecheck at all.
 * All three are killed here.
 */
describe("MaintenanceSection — Re-check Scanned Files", () => {
  beforeEach(() => { vi.unstubAllGlobals(); });

  const recheckButton = () =>
    screen.getByRole("button", { name: /RE-CHECK SCANNED FILES|CLICK AGAIN TO CONFIRM/ });

  const triggers = () => fetch.mock.calls
    .filter(([url]) => String(url).includes("/api/scan/trigger"))
    .map(([, opts]) => JSON.parse(opts.body));

  const withTrigger = (response) => {
    mockApi();
    const inner = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url, opts) =>
      String(url).includes("/api/scan/trigger") ? response : inner(url, opts)));
  };

  it("starts nothing on the first click", async () => {
    withTrigger({ ok: true, status: 200, json: async () => ({}) });
    setup();
    await act(async () => {});

    fireEvent.click(recheckButton());

    expect(recheckButton()).toHaveTextContent("CLICK AGAIN TO CONFIRM");
    expect(triggers()).toEqual([]);
  });

  it("asks for a re-check, not a full rescan, on the second", async () => {
    withTrigger({ ok: true, status: 200, json: async () => ({}) });
    const { toast } = setup();
    await act(async () => {});

    fireEvent.click(recheckButton());
    await act(async () => { fireEvent.click(recheckButton()); });

    expect(triggers()).toEqual([{ recheck: true }]);
    expect(toast).toHaveBeenCalledWith(
      "Re-check started — progress shown in the header", "notice");
  });

  it("says so when a scan is already running", async () => {
    withTrigger({ ok: false, status: 409, json: async () => ({}) });
    const { toast } = setup();
    await act(async () => {});

    fireEvent.click(recheckButton());
    await act(async () => { fireEvent.click(recheckButton()); });

    expect(toast).toHaveBeenCalledWith("A scan is already in progress", "error");
  });
});
