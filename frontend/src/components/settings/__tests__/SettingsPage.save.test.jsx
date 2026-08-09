/**
 * SettingsPage — what save() actually PUTs.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * save() sent EVERY schema key from `values`, a snapshot taken at page load.
 * Two schema fields are also written from outside this page:
 *
 *   dry_run_mode      — toggled from AppHeader, which renders on every page
 *   auto_start_jobs   — toggled from AppHeader, and set to False server-side
 *                       by abort_job as a safety stop
 *
 * So: open Settings, toggle DRY RUN in the header (backend now true, toast
 * confirms), change any unrelated field, press SAVE — and the PUT carries
 * dry_run_mode:false from the load-time snapshot. Dry run silently switches
 * off. The same shape re-enables auto_start_jobs after an abort, undoing the
 * stop the abort button exists to apply.
 *
 * BackupRestoreSection's comment already diagnosed this exact mechanism and
 * fixed it for the import trigger only. This pins the general rule: the
 * request body contains the keys the user changed on this page, and nothing
 * else.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";

/* All four are genuinely group "Worker" in the real SETTINGS_SCHEMA, which
   matters: dry_run_mode and auto_start_jobs sitting in the same category as
   an ordinary numeric setting is exactly what makes the bug reachable in one
   screen. The page renders one category at a time, so the tests select the
   Worker tab below. */
const SCHEMA = [
  { key: "dry_run_mode", label: "Dry run mode", type: "boolean", group: "Worker" },
  { key: "auto_start_jobs", label: "Auto start jobs", type: "boolean", group: "Worker" },
  { key: "und_audio_threshold", label: "Undefined audio threshold", type: "integer", min: 1, group: "Worker" },
  { key: "job_timeout_minutes", label: "Job timeout minutes", type: "integer", group: "Worker" },
];

/** Values as the page loads them. dry_run_mode is false here; the scenario is
 *  that the header turns it ON afterwards, without this page knowing. */
const LOADED = {
  dry_run_mode: false,
  auto_start_jobs: true,
  und_audio_threshold: 2,
  job_timeout_minutes: 120,
};

let puts;

function mockApi({ values = LOADED } = {}) {
  puts = [];
  vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
    const u = String(url);
    if (opts.method === "PUT") {
      puts.push({ url: u, body: JSON.parse(opts.body) });
      return { ok: true, json: async () => ({}) };
    }
    if (u.includes("/api/settings/schema")) {
      return { ok: true, json: async () => SCHEMA };
    }
    if (u.match(/\/api\/settings\/?$/)) {
      return { ok: true, json: async () => values };
    }
    // status pollers used by the page's sub-sections
    return { ok: true, json: async () => ({ value: null, count: 0 }) };
  }));
}

async function renderPage() {
  // The page persists the selected category and renders only that one, so
  // pick Worker before mounting rather than clicking through the tabs.
  localStorage.setItem("remuxarr.settingsCategory", "worker");
  render(
    <ThemeProvider>
      <SettingsPage api="" toast={() => {}} />
    </ThemeProvider>,
  );
  // Wait for the schema/values load to land.
  await screen.findByLabelText("Undefined audio threshold");
}

describe("SettingsPage.save", () => {
  beforeEach(() => mockApi());

  it("sends only the field the user changed", async () => {
    const user = userEvent.setup();
    await renderPage();

    const input = screen.getByLabelText("Undefined audio threshold");
    await user.clear(input);
    await user.type(input, "3");

    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body).toEqual({ und_audio_threshold: 3 });
  });

  it("does not send dry_run_mode, which the header owns", async () => {
    const user = userEvent.setup();
    await renderPage();

    const input = screen.getByLabelText("Undefined audio threshold");
    await user.clear(input);
    await user.type(input, "3");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(
      Object.keys(puts[0].body),
      "a stale dry_run_mode in the payload silently overwrites a header toggle",
    ).not.toContain("dry_run_mode");
  });

  it("does not send auto_start_jobs, which abort_job owns", async () => {
    const user = userEvent.setup();
    await renderPage();

    const input = screen.getByLabelText("Undefined audio threshold");
    await user.clear(input);
    await user.type(input, "3");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(
      Object.keys(puts[0].body),
      "re-enabling auto_start_jobs undoes the safety stop applied by abort",
    ).not.toContain("auto_start_jobs");
  });

  it("sends both fields when the user changed two", async () => {
    const user = userEvent.setup();
    await renderPage();

    const und = screen.getByLabelText("Undefined audio threshold");
    await user.clear(und);
    await user.type(und, "4");
    const timeout = screen.getByLabelText("Job timeout minutes");
    await user.clear(timeout);
    await user.type(timeout, "45");

    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].body).toEqual({ und_audio_threshold: 4, job_timeout_minutes: 45 });
  });

  it("PUTs the declared path, without a redirect-triggering slash mismatch", async () => {
    const user = userEvent.setup();
    await renderPage();

    const input = screen.getByLabelText("Undefined audio threshold");
    await user.clear(input);
    await user.type(input, "3");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(puts).toHaveLength(1));
    // The backend route is PUT /api/settings/ — requesting /api/settings
    // works only via a 307, i.e. two round trips carrying the full body.
    expect(puts[0].url).toMatch(/\/api\/settings\/$/);
  });
});
