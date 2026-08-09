/**
 * SettingsPage — the two settings the header also owns.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * dry_run_mode and auto_start_jobs have a control in two places: the app
 * header, and the Settings page. They used to have two different commit
 * models as well — the header applied on click with optimistic rollback,
 * Settings staged until Save — and two separate copies of the value.
 *
 * That produced a one-way sync bug. useAppData lives above the page switch
 * and never remounts, so a change made in Settings never reached the header
 * until a full browser reload. The reverse worked by accident: SettingsPage
 * is conditionally rendered, so it unmounts and refetches on every tab
 * change. There is no settings_changed WebSocket event to close the gap —
 * the backend emits thirteen event types and none of them concern settings.
 *
 * The same split caused a worse bug: save() sent the page's load-time
 * snapshot for every schema key, so toggling dry run in the header and then
 * saving any unrelated field silently switched it back off.
 *
 * Both are structural, so the fix is structural: these two fields render from
 * the app-level state and commit through the SAME action the header calls.
 * One value, not two copies. These tests pin that.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";

const SCHEMA = [
  { key: "dry_run_mode", label: "Dry run mode", type: "boolean", group: "Worker" },
  { key: "auto_start_jobs", label: "Auto start jobs", type: "boolean", group: "Worker" },
  { key: "und_audio_threshold", label: "Undefined audio threshold", type: "integer", min: 1, group: "Worker" },
];

const LOADED = {
  dry_run_mode: false,
  auto_start_jobs: true,
  und_audio_threshold: 2,
};

let puts;

function mockApi() {
  puts = [];
  vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
    const u = String(url);
    if (opts.method === "PUT") {
      puts.push({ url: u, body: JSON.parse(opts.body) });
      return { ok: true, json: async () => ({}) };
    }
    if (u.includes("/api/settings/schema")) return { ok: true, json: async () => SCHEMA };
    if (u.match(/\/api\/settings\/?$/)) return { ok: true, json: async () => LOADED };
    return { ok: true, json: async () => ({ value: null, count: 0 }) };
  }));
}

/** Stands in for App: holds the live state the header renders from. */
function makeLiveToggles(state, onToggle) {
  return {
    dry_run_mode: { value: state.dryRun, onToggle: onToggle.dryRun },
    auto_start_jobs: { value: state.autoStart, onToggle: onToggle.autoStart },
  };
}

async function renderPage(liveToggles) {
  localStorage.setItem("remuxarr.settingsCategory", "worker");
  render(
    <ThemeProvider>
      <SettingsPage api="" toast={() => {}} liveToggles={liveToggles} />
    </ThemeProvider>,
  );
  await screen.findByLabelText("Undefined audio threshold");
}

describe("SettingsPage live toggles", () => {
  beforeEach(() => mockApi());

  it("renders the header's value, not the page's loaded snapshot", async () => {
    // The header turned dry run ON after this page loaded. The page's own
    // fetch still says false; the header state is the truth.
    await renderPage(makeLiveToggles(
      { dryRun: true, autoStart: true },
      { dryRun: vi.fn(), autoStart: vi.fn() },
    ));

    expect(screen.getByLabelText("Dry run mode")).toBeChecked();
  });

  it("toggling in Settings calls the same action the header calls", async () => {
    const user = userEvent.setup();
    const dryRun = vi.fn();
    await renderPage(makeLiveToggles(
      { dryRun: false, autoStart: true },
      { dryRun, autoStart: vi.fn() },
    ));

    await user.click(screen.getByLabelText("Dry run mode"));

    // Not set() on a local snapshot — the shared action, which owns both the
    // network write and the app-level state the header reads.
    expect(dryRun).toHaveBeenCalledTimes(1);
  });

  it("auto_start_jobs goes through the shared action too", async () => {
    const user = userEvent.setup();
    const autoStart = vi.fn();
    await renderPage(makeLiveToggles(
      { dryRun: false, autoStart: true },
      { dryRun: vi.fn(), autoStart },
    ));

    await user.click(screen.getByLabelText("Auto start jobs"));

    expect(autoStart).toHaveBeenCalledTimes(1);
  });

  it("toggling does not mark the page dirty", async () => {
    const user = userEvent.setup();
    const onDirtyChange = vi.fn();
    localStorage.setItem("remuxarr.settingsCategory", "worker");
    render(
      <ThemeProvider>
        <SettingsPage
          api=""
          toast={() => {}}
          onDirtyChange={onDirtyChange}
          liveToggles={makeLiveToggles(
            { dryRun: false, autoStart: true },
            { dryRun: vi.fn(), autoStart: vi.fn() },
          )}
        />
      </ThemeProvider>,
    );
    await screen.findByLabelText("Undefined audio threshold");
    onDirtyChange.mockClear();

    await user.click(screen.getByLabelText("Dry run mode"));

    // The change is already saved, so claiming an unsaved change would be a
    // lie — and would arm the navigation guard and the beforeunload prompt.
    expect(onDirtyChange).not.toHaveBeenCalledWith(true);
  });

  it("toggling issues no bulk PUT of its own", async () => {
    const user = userEvent.setup();
    await renderPage(makeLiveToggles(
      { dryRun: false, autoStart: true },
      { dryRun: vi.fn(), autoStart: vi.fn() },
    ));

    await user.click(screen.getByLabelText("Dry run mode"));

    // The shared action does its own single-key PUT; this page must not also
    // send one, or the two writes race.
    expect(puts.filter(p => p.url.match(/\/api\/settings\/$/))).toHaveLength(0);
  });

  it("a later save never carries the live-toggle keys", async () => {
    const user = userEvent.setup();
    await renderPage(makeLiveToggles(
      { dryRun: true, autoStart: false },
      { dryRun: vi.fn(), autoStart: vi.fn() },
    ));

    const input = screen.getByLabelText("Undefined audio threshold");
    await user.clear(input);
    await user.type(input, "3");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(puts.some(p => p.body.und_audio_threshold === 3)).toBe(true));
    const bulk = puts.find(p => p.body.und_audio_threshold === 3);
    expect(Object.keys(bulk.body)).toEqual(["und_audio_threshold"]);
  });

  it("labels the rows as applying immediately", async () => {
    await renderPage(makeLiveToggles(
      { dryRun: false, autoStart: true },
      { dryRun: vi.fn(), autoStart: vi.fn() },
    ));

    // Without an affordance these rows look identical to their neighbours
    // while behaving differently, which reads as a bug.
    expect(screen.getAllByText(/applies immediately/i)).toHaveLength(2);
  });

  it("ordinary fields are unaffected and still stage until save", async () => {
    const user = userEvent.setup();
    await renderPage(makeLiveToggles(
      { dryRun: false, autoStart: true },
      { dryRun: vi.fn(), autoStart: vi.fn() },
    ));

    const input = screen.getByLabelText("Undefined audio threshold");
    await user.clear(input);
    await user.type(input, "3");

    // Nothing sent yet — this one still waits for Save.
    expect(puts).toHaveLength(0);
  });

  it("works with no liveToggles prop, so the page stands alone", async () => {
    const user = userEvent.setup();
    await renderPage(undefined);

    // Falls back to the staged behaviour rather than crashing.
    await user.click(screen.getByLabelText("Dry run mode"));
    expect(screen.queryAllByText(/applies immediately/i)).toHaveLength(0);
  });
});
