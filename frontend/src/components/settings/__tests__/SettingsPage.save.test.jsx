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
 * matters: dry_run_mode and auto_start_jobs sitting in the same category as
 * an ordinary numeric setting is exactly what makes the bug reachable in one
 * screen. The page renders one category at a time, so the tests select the
 * Worker tab below. */
const SCHEMA = [
  { key: "dry_run_mode", label: "Dry run mode", type: "boolean", group: "Worker" },
{ key: "auto_start_jobs", label: "Auto start jobs", type: "boolean", group: "Worker" },
{ key: "und_audio_threshold", label: "Undefined audio threshold", type: "integer", min: 1, group: "Worker" },
{ key: "job_timeout_minutes", label: "Job timeout minutes", type: "integer", group: "Worker" },
/* A string field, because the integer control cannot show whether an
 *  in-flight edit was kept: IntegerInput holds a draft until blur, so its box
 *  goes on displaying what was typed whether or not `values` still holds it.
 *  A string input renders straight from props, so what it shows IS the
 *  state. */
{ key: "email_from_address", label: "From address", type: "string", group: "Worker" },
/* A list field, so the "follows the server" rule below is pinned for a
 *  reference type and not only for numbers. scan_paths is the real one. */
{ key: "scan_paths", label: "Scan paths", type: "string_list", group: "Worker" },
];

/** Values as the page loads them. dry_run_mode is false here; the scenario is
 *  that the header turns it ON afterwards, without this page knowing. */
const LOADED = {
  dry_run_mode: false,
  auto_start_jobs: true,
  und_audio_threshold: 2,
  job_timeout_minutes: 120,
  email_from_address: "remuxarr@example.com",
  scan_paths: ["/media/tv"],
};

let puts;
/** Resolver that completes a held PUT. Null until a held PUT is in flight. */
let releasePut;

/**
 * holdPut        keeps the PUT open so a test can type while it is in flight.
 * valuesAfterPut what GET /api/settings/ answers once a PUT has been seen,
 *                standing in for a key changed from outside this page.
 */
function mockApi({ values = LOADED, holdPut = false, valuesAfterPut = null } = {}) {
  puts = [];
  releasePut = null;
  let sawPut = false;
  vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
    const u = String(url);
    if (opts.method === "PUT") {
      puts.push({ url: u, body: JSON.parse(opts.body) });
      sawPut = true;
      if (holdPut) await new Promise((res) => { releasePut = res; });
      return { ok: true, json: async () => ({}) };
    }
    if (u.includes("/api/settings/schema")) {
      return { ok: true, json: async () => SCHEMA };
    }
    if (u.match(/\/api\/settings\/?$/)) {
      return { ok: true, json: async () => (sawPut && valuesAfterPut ? valuesAfterPut : values) };
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

/**
 * save() finishes with a reload so the keys it deliberately did not send are
 * re-read from the server rather than left at the load-time snapshot. That
 * reload used to overwrite anything typed while the PUT was open.
 *
 * A text field silently reverted. An integer field did something worse:
 * IntegerInput holds a draft string until blur, so the box kept showing what
 * was typed while `values` underneath it went back to the server's number —
 * the page then measured itself as clean and the next save sent nothing at
 * all, with the discarded edit still on screen.
 *
 * The mock answers every GET with LOADED, so a value the page still shows
 * after the reload is one the merge kept, not one the server returned.
 */
describe("SettingsPage.save — an edit made while the PUT is in flight", () => {
  const startSaveAndHold = async (user) => {
    const und = screen.getByLabelText("Undefined audio threshold");
    await user.clear(und);
    await user.type(und, "9");
    await user.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(releasePut).toBeTruthy());
  };

  it("is still on screen in a text field after the reload", async () => {
    mockApi({ holdPut: true });
    const user = userEvent.setup();
    await renderPage();
    await startSaveAndHold(user);

    const from = screen.getByLabelText("From address");
    await user.clear(from);
    await user.type(from, "typed@during.save");

    releasePut();

    // und_audio_threshold WAS sent, so it follows the server back to 2 —
    // which is also how this waits for the reload to have landed.
    await waitFor(() =>
    expect(screen.getByLabelText("Undefined audio threshold")).toHaveValue(2));
    expect(screen.getByLabelText("From address")).toHaveValue("typed@during.save");
  });

  it("is held in state for an integer field, not just left in its draft", async () => {
    mockApi({ holdPut: true });
    const user = userEvent.setup();
    await renderPage();
    await startSaveAndHold(user);

    const timeout = screen.getByLabelText("Job timeout minutes");
    await user.clear(timeout);
    await user.type(timeout, "45");

    releasePut();

    await waitFor(() =>
    expect(screen.getByLabelText("Undefined audio threshold")).toHaveValue(2));
    /* Asserting the box reads 45 would pass either way — the draft survives a
     *    reload that discards the value behind it, which is precisely how this
     *    failure hid. The dirty count is the assertion that can tell them apart:
     *    an edit the page has actually kept measures as unsaved against the
     *    baseline, and a discarded one reads as nothing pending. */
    expect(screen.getByText(/1 UNSAVED CHANGE/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save/i })).toBeEnabled();
  });

  it("does not stop an untouched key from following the server", async () => {
    // The reason the reload exists at all: job_timeout_minutes was changed
    // from outside this page, and nothing was typed here while the PUT was
    // open, so the server's value must win. Preserving every key rather than
    // only the edited ones would reinstate exactly the bug save() was
    // narrowed to avoid.
    mockApi({
      holdPut: true,
      valuesAfterPut: { ...LOADED, job_timeout_minutes: 999 },
    });
    const user = userEvent.setup();
    await renderPage();
    await startSaveAndHold(user);

    releasePut();

    await waitFor(() =>
    expect(screen.getByLabelText("Job timeout minutes")).toHaveValue(999));
  });

  it("does not strand a list setting, whose array is a new object each load", async () => {
    // Same rule as the test above, for a list rather than a number. Worth its
    // own case because over-preserving is silent here: a stranded list keeps
    // rendering plausible chips, where a stranded number is at least a value
    // someone might recognise as stale.
    //
    // Note this does NOT depend on the comparison being by value — between a
    // save starting and its reload landing, `prev` is only ever the snapshot
    // with shallow spreads over it, so an untouched array is still the same
    // object and a reference check agrees. The comparison matches the one
    // dirtyKeys uses a few lines above rather than being load-bearing here.
    mockApi({
      holdPut: true,
      valuesAfterPut: { ...LOADED, scan_paths: ["/media/tv", "/media/films"] },
    });
    const user = userEvent.setup();
    await renderPage();
    await startSaveAndHold(user);

    releasePut();

    await waitFor(() => expect(screen.getByText("/media/films")).toBeInTheDocument());
  });
});
