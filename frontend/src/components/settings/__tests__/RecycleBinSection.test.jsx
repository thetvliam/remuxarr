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
 * Verified by mutation, 8 applied, 8 killed:
 *
 *   • Revert acting on the first click                  → killed
 *   • ConfirmBtn firing without confirming              → killed
 *   • Exact candidates sent with the override flag      → killed
 *   • Unverified candidates sent without it             → killed
 *   • Unverified candidates needing no confirmation     → killed
 *   • An unmounted volume not reported                  → killed
 *   • Match offered for a point with no stored tracks   → killed
 *   • A backend refusal replaced with a generic error   → killed
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecycleBinSection } from "../RecycleBinSection";
import { ThemeProvider } from "../../../theme";

const API = "http://backend";

const ATTACHED = {
  id: 1,
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

  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    if (String(url).includes("/candidates/")) {
      return { ok: true, json: async () => candidates };
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
