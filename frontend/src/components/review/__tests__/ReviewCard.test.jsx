/**
 * The review card: one question, its files, and the choices per track.
 *
 * The card is presentational. Everything staged comes in as props and every
 * change goes out as a callback, so what these tests pin is what the card
 * offers, what it marks as chosen, and what it reports — not what happens
 * next.
 *
 * The file is new, so nothing imported it when these were written and every
 * mutation below survived by construction. Each is killed here:
 *
 *   • every track offered all three choices
 *   • a failed extraction offered Extract again
 *   • a file row showing the card's answer as its own
 *   • a file's answer, or the card's, sent for the wrong track
 *   • Use group choice doing nothing
 *   • the Skip button never reporting the card
 *   • no choice ever marked as chosen
 *   • the outcome line never shown
 *   • Extract wearing the theme accent instead of blue
 *
 * The last one matters more than a colour usually would. Extract is a third
 * outcome, and this app's accent is amber, which sits right beside the
 * yellow Keep uses: two choices that look alike are worse than no colour at
 * all. The test reads both tokens from the theme rather than a hex, so it
 * keeps meaning what it says when a theme changes.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReviewCard } from "../ReviewCard";
import { useTheme } from "../../../theme";

const STYLED = { codec: "ass", language: "jpn", is_forced: false,
                 reason: "styled", title: "Signs & Songs" };
const IMAGE = { codec: "hdmv_pgs_subtitle", language: "eng", is_forced: false,
                reason: "image", title: null };
const ENCODING = { codec: "subrip", language: "eng", is_forced: false,
                   reason: "encoding", title: null };

const group = (tracks, files = [{ file_id: 1, filename: "ep01.mkv", streams: [2] }]) => ({
  key: "k", heading: "Show / Season 1", directory: "/media/tv/Show/Season 1",
  file_count: files.length, font_attachments: 17, tracks, files,
});

let tokens;
const Tokens = () => { tokens = useTheme().palette; return null; };

/** Inline styles come back normalised, so a token has to be converted to compare. */
const asRgb = hex => {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  return `rgb(${r}, ${g}, ${b})`;
};

function show(props = {}, tracks = [STYLED]) {
  const handlers = {
    onChoose: vi.fn(), onChooseForFile: vi.fn(),
    onUseGroupChoice: vi.fn(), onToggleSkip: vi.fn(),
  };
  render(
    <>
      <Tokens />
      <ReviewCard group={group(tracks)} {...handlers} {...props} />
    </>,
  );
  return handlers;
}

const choiceNames = () =>
  screen.getAllByRole("button")
        .map(b => b.textContent)
        .filter(t => ["Keep", "Extract SRT", "Delete"].includes(t));

describe("what a track is offered", () => {
  it("offers Keep, Extract SRT and Delete for a styled track", () => {
    show();

    expect(choiceNames()).toEqual(["Keep", "Extract SRT", "Delete"]);
  });

  it.each([
    ["an image track", IMAGE],
    ["a track whose extraction failed", ENCODING],
  ])("offers only Keep and Delete for %s", (_label, track) => {
    show({}, [track]);

    expect(choiceNames()).toEqual(["Keep", "Delete"]);
  });

  it("marks the chosen one and reports which track it belongs to", async () => {
    const user = userEvent.setup();
    const handlers = show({ answers: { 1: "remove" } }, [STYLED, IMAGE]);

    const chosen = screen.getAllByRole("button", { pressed: true });
    expect(chosen).toHaveLength(1);
    expect(chosen[0].textContent).toBe("Delete");

    await user.click(screen.getAllByRole("button", { name: "Keep" })[1]);
    expect(handlers.onChoose).toHaveBeenCalledWith(1, "keep");
  });

  it("gives Extract a colour of its own, not the accent Keep sits beside", () => {
    show();

    const extract = screen.getByRole("button", { name: "Extract SRT" });
    expect(tokens.accent).not.toBe(tokens.blue);
    expect(extract.style.color).toBe(asRgb(tokens.blue));
  });
});

describe("a file set on its own", () => {
  const FILES = [
    { file_id: 1, filename: "ep01.mkv", streams: [2, 3] },
    { file_id: 2, filename: "ep02.mkv", streams: [4, 5] },
  ];

  function openFirstFile(props = {}) {
    const handlers = {
      onChoose: vi.fn(), onChooseForFile: vi.fn(),
      onUseGroupChoice: vi.fn(), onToggleSkip: vi.fn(),
    };
    render(
      <>
        <Tokens />
        <ReviewCard group={{ ...group([STYLED, IMAGE]), files: FILES, file_count: 2 }}
                    {...handlers} {...props} />
      </>,
    );
    return handlers;
  }

  it("reports the file and the track it was set for", async () => {
    const user = userEvent.setup();
    const handlers = openFirstFile({ answers: { 0: "keep", 1: "keep" } });

    await user.click(screen.getByRole("button", { name: /Show files/ }));
    await user.click(screen.getByRole("button", { name: /ep02.mkv/ }));
    // The second track's Delete, inside the opened row.
    const deletes = screen.getAllByRole("button", { name: "Delete" });
    await user.click(deletes[deletes.length - 1]);

    expect(handlers.onChooseForFile).toHaveBeenCalledWith(2, 1, "remove");
  });

  it("shows the file's own answer, not the card's", async () => {
    const user = userEvent.setup();
    openFirstFile({ answers: { 0: "keep", 1: "keep" } });

    await user.click(screen.getByRole("button", { name: /Show files/ }));
    await user.click(screen.getByRole("button", { name: /ep02.mkv/ }));

    // The card's two Keeps are pressed; the opened row has answered nothing,
    // so a row showing the card's choice would report four.
    expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(2);
  });

  it("says which files are set on their own, and offers them the group's choice", async () => {
    const user = userEvent.setup();
    const handlers = openFirstFile({ answers: { 0: "keep", 1: "keep" },
                                     fileAnswers: { 2: { 0: "remove", 1: "remove" } } });

    await user.click(screen.getByRole("button", { name: /Show files/ }));
    expect(screen.getByText("Set on its own")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /ep02.mkv/ }));
    await user.click(screen.getByRole("button", { name: "Use group choice" }));

    expect(handlers.onUseGroupChoice).toHaveBeenCalledWith(2);
  });
});

describe("skipping and the outcome line", () => {
  it("reports a skip rather than acting on it", async () => {
    const user = userEvent.setup();
    const handlers = show();

    await user.click(screen.getByRole("button", { name: "Skip" }));

    expect(handlers.onToggleSkip).toHaveBeenCalled();
  });

  it("puts the choices away once a card is skipped, and says what that means", () => {
    show({ skipped: true });

    expect(choiceNames()).toEqual([]);
    expect(screen.getByText(/asked about again on the next scan/)).toBeInTheDocument();
  });

  it("shows the outcome it is given", () => {
    show({ outcome: "Converts 90 of 96 to MP4, 6 stay MKV" });

    expect(screen.getByText("Converts 90 of 96 to MP4, 6 stay MKV")).toBeInTheDocument();
  });
});
