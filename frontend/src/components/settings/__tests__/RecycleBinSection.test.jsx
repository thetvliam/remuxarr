/**
 * RecycleBinSection — the safety net, inside Settings.
 *
 * WHAT IS ACTUALLY AT RISK HERE
 * -----------------------------
 * Two of the four actions on this panel are irreversible. Revert overwrites
 * a media file; Discard throws away the only copy of the tracks a job
 * removed. Neither should be one stray click away, so both take a second
 * confirming click — and that is worth a test rather than an assumption,
 * because the failure mode is silent: a single-click Discard looks
 * identical until someone loses a file.
 *
 * The other risk is the match flow. An "exact" candidate has the same size
 * and timestamp as the file the job produced, which for a rename is proof
 * rather than a guess, and it attaches without a warning. A "nearby"
 * candidate is a guess, and attaching one sends confirm_mismatch — the flag
 * that lets the backend skip a check it would otherwise refuse on. Sending
 * that flag for an exact match would quietly disable the verification for
 * the case that did not need it, and no visible behaviour would change.
 * Two tests below exist only to keep those apart.
 *
 * Everything else is presentation, and only the parts that would mislead
 * are pinned: an unmounted volume must not read as an empty bin, and a
 * revert point whose stored tracks are gone must not offer to match.
 *
 * Verified by mutation, 17 applied, 17 killed:
 *
 *   • Revert acting on the first click                  → killed
 *   • ConfirmBtn firing without confirming              → killed
 *   • Exact candidates sent with the override flag      → killed
 *   • Unverified candidates sent without it             → killed
 *   • Unverified candidates needing no confirmation     → killed
 *   • An unmounted volume not reported                  → killed
 *   • Match offered for a point with no stored tracks   → killed
 *   • A backend refusal replaced with a generic error   → killed
 *   • The running revert not held locally before the
 *     request goes out                                  → killed
 *   • Other entries still offering revert while one runs → killed
 *   • A running revert still showing a REVERT button     → killed
 *   • An in-flight revert not read back on load          → killed
 *   • Attach refusal reasons dropped, summary only        → killed
 *   • Structured refusals routed back to the toast        → killed
 *   • A string refusal swallowed by the panel             → killed
 *   • Bulk discards left usable during a revert            → killed
 *
 * That last one survived at first. The panel's handler is only passed by
 * attach, so removing the "is this structured?" check left restore's
 * string refusal toasting exactly as before and nothing failed — the gap
 * was an attach that fails with a bare string, such as a 404 for a point
 * another client already discarded.
 *   • A refused start leaving the row stuck              → killed
 *
 * The first and last of those survived at first. Both concern the window
 * between sending the request and hearing back from /status, and both
 * tests waited it out with findByRole — which passes whether or not the
 * row was ever marked, because the reload corrects it either way. They
 * assert synchronously now, against a deliberately delayed /status.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecycleBinSection } from "../RecycleBinSection";
import { ThemeProvider } from "../../../theme";
import { CONFIRM_MS } from "../../../constants";

const API = "http://backend";

const ATTACHED = {
  id: 1,
  restorable: true,
  blocked_reason: null,
  file_id: 7,
  current_path: "/media/tv/Show/S01E01.mkv",
  current_filename: "S01E01.mkv",
  original_path: "/media/tv/Show/S01E01.mkv",
  sidecar_size: 52428800,
  created_at: "2026-08-01T10:00:00Z",
  detached_at: null,
};

const DETACHED = {
  id: 2,
  original_path: "/media/tv/Show/Old Name.mkv",
  original_filename: "Old Name.mkv",
  detached_at: "2026-08-10T10:00:00Z",
  created_at: "2026-08-01T10:00:00Z",
  sidecar_size: 10485760,
  sidecar_present: true,
  duration: 1420,
  stored_tracks: [{ type: "audio", codec: "aac", language: "jpn" }],
};

let calls;

const mockFetch = (overrides = {}) => {
  const listing = {
    recycle_bin_ready: true,
    recycle_bin_reason: "",
    attached: [ATTACHED],
    detached: [DETACHED],
    ...overrides.listing,
  };
  const candidates = overrides.candidates ?? {
    exact: [{ id: 9, path: "/media/tv/Show/New Name.mkv", filename: "New Name.mkv", size: 100 }],
    nearby: [{ id: 10, path: "/media/tv/Show/Other.mkv", filename: "Other.mkv", size: 100 }],
  };

  // Stateful, because the API is: starting a revert makes /status report
  // it as running until it finishes. A mock that always answers "idle"
  // contradicts the endpoint it stands in for, and the panel reads that
  // answer back immediately after every POST.
  let started = null;

  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    if (String(url).includes("/candidates/")) {
      return { ok: true, json: async () => candidates };
    }
    if (String(url).endsWith("/status")) {
      if (overrides.statusDelayMs) {
        await new Promise(r => setTimeout(r, overrides.statusDelayMs));
      }
      if (overrides.status) return { ok: true, json: async () => overrides.status };
      return {
        ok: true,
        json: async () => ({ running: started !== null, point_id: started }),
      };
    }
    const restore = String(url).match(/\/api\/revert\/(\d+)\/restore\//);
    if (restore && (options.method || "GET") === "POST") {
      if (overrides.restoreFails) {
        return { ok: false, json: async () => ({ detail: "A revert is already running" }) };
      }
      started = Number(restore[1]);
      return { ok: true, json: async () => ({ status: "started" }) };
    }
    if (String(url).includes("/attach/") && overrides.attachRefusal) {
      return { ok: false, json: async () => ({ detail: overrides.attachRefusal }) };
    }
    if ((options.method || "GET") !== "GET") {
      return { ok: true, json: async () => ({ status: "ok" }) };
    }
    return { ok: true, json: async () => listing };
  });
};

const setup = (overrides) => {
  mockFetch(overrides);
  const toast = vi.fn();
  render(
    <ThemeProvider>
    <RecycleBinSection api={API} toast={toast} reloadKey={0} />
    </ThemeProvider>,
  );
  return { toast };
};

const bodyOf = (fragment) =>
JSON.parse(calls.find(c => String(c.url).includes(fragment) && c.method === "POST").body);

beforeEach(() => { calls = []; });

/* ── Destructive actions need two clicks ─────────────────────────────────── */

describe("confirmation", () => {
  it("disarms itself once the confirmation window lapses", async () => {
    /* ConfirmBtn's timeout had no test: deleting it outright left all 466
     * tests passing. Every destructive action in this panel goes through
     * this one component, so an armed button that never stands down is one
     * stray click from a revert or a discard the user had walked away from.
     *
     * The constant rather than a literal — a number here would drift from
     * the source the way this component's neighbours already have. fireEvent
     * rather than userEvent, which schedules its own work on the timers this
     * replaces and deadlocks with them. */
    setup();
    const revert = await screen.findByRole("button", { name: "REVERT" });

    vi.useFakeTimers();
    try {
      fireEvent.click(revert);
      expect(screen.getByRole("button", { name: "CONFIRM REVERT" })).toBeTruthy();

      act(() => { vi.advanceTimersByTime(CONFIRM_MS - 100); });
      expect(screen.getByRole("button", { name: "CONFIRM REVERT" })).toBeTruthy();

      act(() => { vi.advanceTimersByTime(200); });
      expect(screen.getByRole("button", { name: "REVERT" })).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
    expect(calls.some(c => c.url.includes("/restore/"))).toBe(false);
  });

  it("does not revert on the first click", async () => {
    setup();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));

    expect(calls.some(c => c.url.includes("/restore/"))).toBe(false);
    expect(screen.getByRole("button", { name: "CONFIRM REVERT" })).toBeTruthy();
  });

  it("reverts on the second click", async () => {
    setup();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));
    await user.click(screen.getByRole("button", { name: "CONFIRM REVERT" }));

    await waitFor(() =>
    expect(calls.some(c => c.url.includes("/api/revert/1/restore/")
    && c.method === "POST")).toBe(true));
  });

  it("does not discard on the first click", async () => {
    setup();
    const user = userEvent.setup();

    const discards = await screen.findAllByRole("button", { name: "DISCARD" });
    await user.click(discards[0]);

    expect(calls.some(c => c.method === "DELETE")).toBe(false);
  });

  it("does not empty the bin on the first click", async () => {
    setup();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "EMPTY RECYCLE BIN" }));

    expect(calls.some(c => c.method === "DELETE")).toBe(false);
  });
});

/* ── The match flow ──────────────────────────────────────────────────────── */

describe("matching", () => {
  it("attaches an exact candidate without asking to override anything", async () => {
    setup();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "MATCH" }));
    await user.click(await screen.findByRole("button", { name: "USE THIS FILE" }));

    await waitFor(() => expect(calls.some(c => c.url.includes("/attach/"))).toBe(true));
    expect(bodyOf("/attach/")).toEqual({ file_id: 9, confirm_mismatch: false });
  });

  it("sends the override only for an unverified candidate", async () => {
    setup();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "MATCH" }));
    await user.click(await screen.findByRole("button", { name: "USE ANYWAY" }));
    await user.click(await screen.findByRole("button", { name: "CONFIRM — UNVERIFIED" }));

    await waitFor(() => expect(calls.some(c => c.url.includes("/attach/"))).toBe(true));
    expect(bodyOf("/attach/")).toEqual({ file_id: 10, confirm_mismatch: true });
  });

  it("needs a second click before attaching an unverified candidate", async () => {
    setup();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "MATCH" }));
    await user.click(await screen.findByRole("button", { name: "USE ANYWAY" }));

    expect(calls.some(c => c.url.includes("/attach/"))).toBe(false);
  });

  it("says so when nothing plausible was found", async () => {
    setup({ candidates: { exact: [], nearby: [] } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "MATCH" }));

    expect(await screen.findByText(/No candidates found/i)).toBeTruthy();
  });
});

/* ── One revert at a time ────────────────────────────────────────────────── */

describe("concurrency", () => {
  it("shows the running revert instead of offering it again", async () => {
    // The POST returns as soon as the revert has STARTED, so the reload it
    // triggers races the work. Without holding the state locally the row
    // offers REVERT again on a file already being rewritten.
    setup();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));
    await user.click(screen.getByRole("button", { name: "CONFIRM REVERT" }));

    expect(await screen.findByRole("button", { name: "REVERTING…" })).toBeTruthy();
  });

  it("does not offer revert on other entries while one is running", async () => {
    // Clicking them is what the user reported: only the first ran, the
    // rest were refused with a toast that is easy to miss, and every entry
    // stayed on screen looking untouched.
    setup({
      listing: {
        attached: [ATTACHED, { ...ATTACHED, id: 3, file_id: 8,
          current_filename: "S01E02.mkv" }],
          detached: [],
      },
      status: { running: true, point_id: 1 },
    });

    await screen.findByRole("button", { name: "REVERTING…" });
    const others = screen.getAllByRole("button", { name: "REVERT" });
    expect(others).toHaveLength(1);
    expect(others[0].disabled).toBe(true);
  });

  it("marks the row immediately, before the server has been asked", async () => {
    /**
     * The status endpoint is the source of truth, but reading it takes a
     * round trip. In between, the row would still offer REVERT on a file
     * already being rewritten — and clicking it earns a 409 and an error
     * toast for doing exactly what the UI invited.
     *
     * The delay here is what makes that window visible at all: with an
     * instantly-resolving mock the status read lands in the same tick and
     * the local update looks redundant.
     */
    setup({ statusDelayMs: 200 });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));
    await user.click(screen.getByRole("button", { name: "CONFIRM REVERT" }));

    // Synchronous on purpose. findByRole would wait out the delay and pass
    // whether or not the row was marked before the request went out, which
    // is precisely the window being tested.
    expect(screen.getByRole("button", { name: "REVERTING…" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "REVERT" })).toBeNull();
  });

  it("clears the row when starting a revert is refused", async () => {
    // The mark above is applied before the request is sent, so a refusal
    // has to undo it — otherwise the row shows REVERTING… for a revert
    // that never began, until something else reloads the panel.
    setup({ restoreFails: true, statusDelayMs: 200 });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));
    await user.click(screen.getByRole("button", { name: "CONFIRM REVERT" }));

    // Synchronous, for the same reason as the test above: the reload that
    // follows would eventually correct the row on its own, so waiting for
    // it proves nothing about the rollback.
    expect(screen.getByRole("button", { name: "REVERT" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "REVERTING…" })).toBeNull();
  });

  it("picks up a revert already running when the panel opens", async () => {
    // Opening Settings mid-revert, or reloading the page, must not show
    // buttons the API will refuse.
    setup({ status: { running: true, point_id: 1 } });

    expect(await screen.findByRole("button", { name: "REVERTING…" })).toBeTruthy();
  });

  it("re-enables the button when starting a revert is refused", async () => {
    const { toast } = setup();
    global.fetch = vi.fn(async (url, options = {}) => {
      calls.push({ url, method: options.method || "GET" });
      if (String(url).endsWith("/status")) {
        return { ok: true, json: async () => ({ running: false, point_id: null }) };
      }
      if ((options.method || "GET") !== "GET") {
        return { ok: false, json: async () => ({ detail: "This file is processing in the queue." }) };
      }
      return { ok: true, json: async () => ({ recycle_bin_ready: true, attached: [ATTACHED], detached: [] }) };
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));
    await user.click(screen.getByRole("button", { name: "CONFIRM REVERT" }));

    await waitFor(() => expect(toast).toHaveBeenCalled());
    // A refusal must not leave the row stuck showing REVERTING… forever.
    expect(await screen.findByRole("button", { name: "REVERT" })).toBeTruthy();
  });
});

/* ── Things that would mislead ───────────────────────────────────────────── */

describe("presentation", () => {
  it("distinguishes an unmounted volume from an empty bin", async () => {
    setup({
      listing: {
        recycle_bin_ready: false,
        recycle_bin_reason: "/recycle does not exist — the recycle volume does not appear to be mounted.",
        attached: [],
        detached: [],
      },
    });

    expect(await screen.findByText(/does not appear to be mounted/i)).toBeTruthy();
  });

  it("explains why an entry cannot be reverted instead of offering it", async () => {
    /**
     * Sonarr upgrading the episode is the everyday case. The entry stays
     * listed — its stored tracks are still on the volume and still taking
     * up space, so it has to be visible to be discarded — but Revert on
     * it produces a refusal the user has no way to explain.
     */
    setup({
      listing: {
        attached: [{ ...ATTACHED, restorable: false,
          blocked_reason: "S01E01.mkv has changed size since it was processed." }],
          detached: [],
      },
    });

    expect(await screen.findByText(/has changed size since it was processed/i))
    .toBeTruthy();
    expect(screen.queryByRole("button", { name: "REVERT" })).toBeNull();
    // Discard has to remain: the whole reason to show a dead entry is so
    // the space it occupies can be reclaimed.
    expect(screen.getByRole("button", { name: "DISCARD" })).toBeTruthy();
  });

  it("does not offer to match an entry whose stored tracks are gone", async () => {
    setup({
      listing: {
        attached: [],
        detached: [{ ...DETACHED, sidecar_present: false }],
      },
    });

    expect(await screen.findByText(/no longer restore anything/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "MATCH" })).toBeNull();
  });

  it("warns when reverting will rename the file back", async () => {
    setup({
      listing: {
        attached: [{ ...ATTACHED,
          current_filename: "S01E01.mp4",
          current_path: "/media/tv/Show/S01E01.mp4",
          original_path: "/media/tv/Show/S01E01.mkv" }],
          detached: [],
      },
    });

    expect(await screen.findByText(/restores as S01E01\.mkv/i)).toBeTruthy();
  });

  it("surfaces the backend's reason for a refusal rather than a generic error", async () => {
    // "This file was upgraded since" is actionable; "revert failed" is not.
    const { toast } = setup();
    global.fetch = vi.fn(async (url, options = {}) => {
      calls.push({ url, method: options.method || "GET" });
      if ((options.method || "GET") !== "GET") {
        return {
          ok: false,
          json: async () => ({ detail: "This file is processing in the queue." }),
        };
      }
      return {
        ok: true,
        json: async () => ({ recycle_bin_ready: true, attached: [ATTACHED], detached: [] }),
      };
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));
    await user.click(screen.getByRole("button", { name: "CONFIRM REVERT" }));

    await waitFor(() =>
    expect(toast).toHaveBeenCalledWith("This file is processing in the queue.", "error"));
  });

  it("shows every reason an attach was refused, not just the summary", async () => {
    /* assess() builds these reasons deliberately — which streams are
     * missing, with codecs and languages. The route docstring says a bare
     * "no" just sends the user to try the next candidate at random, which
     * is exactly what happened: the array reached the browser and act()
     * rendered detail.error alone.
     *
     * The distinction being pinned is between the summary and the detail.
     * "Does not belong to this file" is not actionable; "the commentary
     * track is missing" tells the user whether to keep looking. */
    const { toast } = setup({
      attachRefusal: {
        error: "This revert point does not belong to this file",
        tier:  "incompatible",
        reasons: [
          "2 stream(s) the original still had are not in this file: " +
          "audio ac3 [eng], subtitle subrip [dut]. This is not the same content.",
          "Runtime differs by 214s.",
        ],
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "MATCH" }));
    await user.click(await screen.findByRole("button", { name: "USE THIS FILE" }));

    expect(await screen.findByText(/not the same content/)).toBeTruthy();
    expect(screen.getByText(/Runtime differs by 214s/)).toBeTruthy();
    // Shown in the panel, beside the list being chosen from — not as a
    // toast that covers one line and then disappears.
    expect(toast).not.toHaveBeenCalledWith(
      "This revert point does not belong to this file", "error");
  });

  it("still toasts a refusal that carries no reasons to render", async () => {
    /* Guards the fallback: restore refuses with a plain string, and that
     * path must keep working rather than silently rendering nothing. */
    const { toast } = setup({ restoreFails: true });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "REVERT" }));
    await user.click(screen.getByRole("button", { name: "CONFIRM REVERT" }));

    await waitFor(() =>
    expect(toast).toHaveBeenCalledWith("A revert is already running", "error"));
  });

  it("toasts an attach refusal that is a plain string rather than swallowing it", async () => {
    /* attach() itself always refuses with a structured detail, but the
     * route can also fail in ways FastAPI describes with a bare string —
     * a 404 for a point another client discarded, say. Handing that to
     * the panel renders its fallback wording and loses what actually
     * went wrong, so only structured refusals are taken in place. */
    const { toast } = setup({ attachRefusal: "Revert point not found" });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "MATCH" }));
    await user.click(await screen.findByRole("button", { name: "USE THIS FILE" }));

    await waitFor(() =>
    expect(toast).toHaveBeenCalledWith("Revert point not found", "error"));
    expect(screen.queryByText(/That file was not accepted/)).toBeNull();
  });

  it("holds both bulk discards while a revert is running", async () => {
    /* Both sweeps delete sidecars, including the one a running restore is
     * reading from stream by stream. The per-row DISCARD was already held;
     * these two were not, and DISCARD UNMATCHED looks safe only until a
     * rescan detaches a point mid-revert. */
    setup({ status: { running: true, point_id: 1 } });

    const empty = await screen.findByRole("button", { name: "EMPTY RECYCLE BIN" });
    expect(empty.disabled).toBe(true);

    const unmatched = screen.getByRole("button", { name: "DISCARD UNMATCHED" });
    expect(unmatched.disabled).toBe(true);
  });

  it("leaves the bulk discards usable when nothing is running", async () => {
    setup({ status: { running: false, point_id: null } });

    const empty = await screen.findByRole("button", { name: "EMPTY RECYCLE BIN" });
    expect(empty.disabled).toBe(false);
  });

  it("keeps loading, empty and broken apart", async () => {
    global.fetch = vi.fn(async () => { throw new Error("network down"); });
    render(
      <ThemeProvider>
      <RecycleBinSection api={API} toast={vi.fn()} reloadKey={0} />
      </ThemeProvider>,
    );

    expect(await screen.findByText(/Couldn't load the recycle bin/i)).toBeTruthy();
  });
});
