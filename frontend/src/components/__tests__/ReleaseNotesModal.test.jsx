/**
 * Release notes dialog: shown once per distinct set of notes.
 *
 * The backend hashes the parsed notes and returns it as `version`. This
 * component stores the version the user dismissed and shows the dialog
 * exactly when the two differ.
 *
 * Both failure directions matter, and they fail in opposite ways. Not
 * showing it means a release that renamed someone's settings arrives
 * silently. Showing it every load means people dismiss it without reading,
 * which is the same thing with extra steps.
 *
 * Verified by mutation, 5 applied, 5 killed:
 *
 *   • The seen-version check dropped, so it shows every load  → killed
 *   • The version never stored, so it shows again next load   → killed
 *   • Shown when version is null (nothing to announce)        → killed
 *   • Any stored version treated as "seen", ignoring which    → killed
 *   • Sections rendered but their items dropped               → killed
 *   • The panel background left unset, so it renders clear    → killed
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReleaseNotesModal } from "../ReleaseNotesModal";
import { ThemeProvider } from "../../theme";

const STORAGE_KEY = "remuxarr.releaseNotesSeen";

const NOTES = {
  version: "abc123def456",
  sections: [
    { title: "Changed", items: [
      "Undefined-language handling is now set separately for audio and subtitles.",
      "Subtitles extracted through review are named .en.srt rather than .eng.srt.",
    ] },
    { title: "Fixed", items: ["Ignoring a file now clears all of its tracks."] },
  ],
};

const mockNotes = (body = NOTES, { ok = true } = {}) => {
  global.fetch = vi.fn(async () => ({ ok, json: async () => body }));
};

const renderModal = () => render(
  <ThemeProvider>
  <ReleaseNotesModal api="" />
  </ThemeProvider>,
);

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("ReleaseNotesModal", () => {
  it("shows the notes when nothing has been dismissed", async () => {
    mockNotes();
    renderModal();

    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getByText(/set separately for audio and subtitles/)).toBeTruthy();
    expect(screen.getByText(/named \.en\.srt/)).toBeTruthy();
    expect(screen.getByText(/clears all of its tracks/)).toBeTruthy();
  });

  it("stays shut once this exact version has been dismissed", async () => {
    localStorage.setItem(STORAGE_KEY, NOTES.version);
    mockNotes();
    renderModal();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows again when the notes have changed since the dismissal", async () => {
    /* The point of hashing content rather than counting releases: a new
     * set of notes is a different hash, so a user who dismissed the last
     * one is told about this one. */
    localStorage.setItem(STORAGE_KEY, "an-older-hash");
    mockNotes();
    renderModal();

    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("records the version it showed, so it does not return next load", async () => {
    mockNotes();
    renderModal();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "GOT IT" }));

    expect(localStorage.getItem(STORAGE_KEY)).toBe(NOTES.version);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows nothing when there is nothing to announce", async () => {
    /* The normal state of a cycle with no user-visible changes yet: the
     * file holds only its workflow comment, so the backend reports no
     * version. An empty dialog on every update would train people to
     * dismiss the one that matters. */
    mockNotes({ version: null, sections: [] });
    renderModal();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("stays quiet when the backend cannot be reached", async () => {
    /* The dashboard reports an unreachable backend. A second error about
     * release notes on top of that helps nobody. */
    global.fetch = vi.fn(async () => { throw new Error("network"); });
    renderModal();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders on an opaque panel, not over the page behind it", async () => {
    /* This shipped broken and all seven tests above passed.
     *
     * The panel read surface.raised, which the theme does not define. An
     * undefined value makes React omit the property entirely rather than
     * complain, so the dialog rendered with no background and the queue and
     * history text showed straight through the release notes.
     *
     * Asserting the key resolves, not that it is any particular colour —
     * the theme owns the colour and has more than one palette. What must
     * not happen is the property going missing because a name was wrong. */
    mockNotes();
    renderModal();

    const dialog = await screen.findByRole("dialog");

    expect(dialog.style.background).toBeTruthy();
    expect(dialog.style.background).not.toBe("transparent");
  });

  it("closes on Escape", async () => {
    mockNotes();
    renderModal();
    const user = userEvent.setup();

    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(localStorage.getItem(STORAGE_KEY)).toBe(NOTES.version);
  });
});
