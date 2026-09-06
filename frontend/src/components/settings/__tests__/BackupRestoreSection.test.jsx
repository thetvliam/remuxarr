/**
 * BackupRestoreSection — the import confirmation window.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Importing settings overwrites every key present in the file, so it is
 * behind the same two-click confirm as the destructive actions elsewhere.
 * None of it was tested: deleting the auto-cancel outright left all 466
 * tests passing, on the one confirm in the app guarding an action that
 * rewrites the whole configuration.
 *
 * The window here is deliberately not the shared CONFIRM_MS. It asks the
 * user to read a filename and decide whether to overwrite everything they
 * have, which the source records as not a four-second decision, so it runs
 * on IMPORT_CONFIRM_MS instead. Read from the constant rather than written
 * as a number, because a literal is how the two drift apart.
 *
 * What lapsing has to clear is the subtle part, and the reason this is not
 * just "setConfirming(false)". A file input fires no change event when you
 * re-pick the file already in it. So when only `confirming` was reset, the
 * staged file stayed in the ref and the input kept its value — and after
 * letting the window lapse, pressing IMPORT… reopened the picker, choosing
 * the same file did nothing at all, and the button looked broken until the
 * user happened to pick a different one.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackupRestoreSection } from "../BackupRestoreSection";
import { ThemeProvider } from "../../../theme";
import { IMPORT_CONFIRM_MS } from "../../../constants";

const API = "http://backend";

let calls;

const setup = () => {
  const toast = vi.fn();
  const onImported = vi.fn();
  render(
    <ThemeProvider>
      <BackupRestoreSection api={API} toast={toast} onImported={onImported} />
    </ThemeProvider>,
  );
  return { toast, onImported };
};

/** The import button, whatever state it is in. */
const importBtn = () =>
  screen.getByRole("button", { name: /IMPORT…|CLICK AGAIN TO CONFIRM|IMPORTING…/ });

/** The hidden file input, which has no accessible role to query by. */
const fileInput = () => document.querySelector('input[type="file"]');

/** Stage a file the way the picker would. */
const pickFile = (name = "settings.json") => {
  const file = new File(['{"scan_paths":[]}'], name, { type: "application/json" });
  fireEvent.change(fileInput(), { target: { files: [file] } });
  return file;
};

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url, opts) => {
    calls.push({ url: String(url), method: opts?.method });
    return { ok: true, status: 200, json: async () => ({ applied: 3, skipped: 0 }) };
  }));
});

describe("BackupRestoreSection — import confirmation", () => {
  it("arms rather than importing when a file is picked", () => {
    setup();
    pickFile();

    expect(importBtn()).toHaveTextContent(/CLICK AGAIN TO CONFIRM/);
    expect(calls.some(c => c.url.includes("/api/settings/import"))).toBe(false);
  });

  it("imports on the confirming click", async () => {
    setup();
    pickFile();

    await act(async () => { fireEvent.click(importBtn()); });

    expect(calls.some(c => c.url.includes("/api/settings/import") && c.method === "POST")).toBe(true);
  });

  it("disarms itself once the confirmation window lapses", async () => {
    /* Fake timers go in BEFORE the file is picked. Picking is what schedules
     * the timeout, so installing them afterwards leaves a real timer running
     * that advanceTimersByTime does not control — the button then never
     * stands down and the test fails against correct code. */
    vi.useFakeTimers();
    try {
      setup();
      pickFile();
      expect(importBtn()).toHaveTextContent(/CLICK AGAIN TO CONFIRM/);

      act(() => { vi.advanceTimersByTime(IMPORT_CONFIRM_MS - 100); });
      expect(importBtn()).toHaveTextContent(/CLICK AGAIN TO CONFIRM/);

      act(() => { vi.advanceTimersByTime(200); });
      expect(importBtn()).toHaveTextContent(/^IMPORT…$/);
    } finally {
      vi.useRealTimers();
    }
    expect(calls.some(c => c.url.includes("/api/settings/import"))).toBe(false);
  });

  it("a timer from an earlier confirmation cannot disarm a fresh one", async () => {
    /* What the cleanup on the effect is for, and the failure DangerZone's
     * comment calls critical. Confirming an import leaves that window's
     * timer pending; without clearTimeout it fires later and clears whatever
     * has been staged since, so a file picked seconds after an import would
     * silently disarm itself partway through its own window.
     *
     * Timed to land between the two: past the first window, short of the
     * second. */
    vi.useFakeTimers();
    try {
      setup();
      pickFile("first.json");
      await act(async () => { fireEvent.click(importBtn()); });   // confirms, disarms

      act(() => { vi.advanceTimersByTime(IMPORT_CONFIRM_MS - 200); });
      pickFile("second.json");                                     // re-arms
      expect(importBtn()).toHaveTextContent(/CLICK AGAIN TO CONFIRM/);

      // Enough for the FIRST window to have elapsed, not the second.
      act(() => { vi.advanceTimersByTime(300); });
      expect(importBtn()).toHaveTextContent(/CLICK AGAIN TO CONFIRM/);
    } finally {
      vi.useRealTimers();
    }
  });
});
