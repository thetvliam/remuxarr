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
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MaintenanceSection } from "../MaintenanceSection";
import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";
import { CONFIRM_MS } from "../../../constants";

const ORPHAN = {
  id: 7, filename: "movie.mkv", path: "/old/movie.mkv", on_disk: false,
};

/**
 * The three settings reads on mount, then whatever the test needs. Routed by
 * URL rather than by call order: the mount reads are a Promise.all, so their
 * order is not something a test should depend on.
 */
const mockApi = ({ orphaned = [ORPHAN], removeOk = true } = {}) => {
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
        : { ok: false, json: async () => ({ detail: "nope" }) };
    }
    if (u.includes("/api/scan/orphaned"))
      return { ok: true, json: async () => ({ total: orphaned.length, items: orphaned }) };
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

    await waitFor(() => expect(toast).toHaveBeenCalledWith(
      "Failed to remove orphaned files", "error"));
    expect(onRecordsRemoved).not.toHaveBeenCalled();
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
