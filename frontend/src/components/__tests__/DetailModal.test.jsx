/**
 * DetailModal — the Ask again button.
 *
 * Ask again forgets every answer and confirmation stored for the item's file
 * and decides it again (POST /api/queue/{id}/ask-again, see
 * tests/test_ask_again.py). Nothing brings the answers back short of
 * answering again, so the button takes two clicks, through useArmedConfirm
 * as the Settings actions that cannot be undone do. App decides whether to
 * offer it (see App.test.jsx); the modal shows it only when given a handler.
 *
 * Verified by mutation: the confirm step removed, the button never rendered,
 * and the action row still shown only for Retry or Dismiss, all three
 * survived the suite without this file and are killed here.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DetailModal } from "../DetailModal";
import { CONFIRM_MS } from "../../constants";
import { ThemeProvider } from "../../theme";

const ITEM = {
  id: 5, status: "success", reason: "Remove 1 subtitle track",
  file: { id: 1, filename: "Ep1.mkv", path: "/tv/Show/Ep1.mkv", has_answers: true },
  planned_actions: [],
};

const show = (props) => render(
  <ThemeProvider>
    <DetailModal item={ITEM} onClose={() => {}} {...props} />
  </ThemeProvider>,
);

afterEach(() => vi.useRealTimers());

describe("DetailModal — Ask again", () => {
  it("is not shown without a handler", () => {
    show({ onRetry: () => {}, onDismiss: () => {} });

    expect(screen.queryByRole("button", { name: /ASK AGAIN/ })).toBeNull();
  });

  it("asks for a second click before forgetting anything", () => {
    const onAskAgain = vi.fn();
    show({ onAskAgain });

    fireEvent.click(screen.getByRole("button", { name: "↺ ASK AGAIN" }));
    expect(onAskAgain).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "CLICK AGAIN TO CONFIRM" }));
    expect(onAskAgain).toHaveBeenCalledTimes(1);
  });

  it("disarms itself when the second click does not come", () => {
    vi.useFakeTimers();
    const onAskAgain = vi.fn();
    show({ onAskAgain });

    fireEvent.click(screen.getByRole("button", { name: "↺ ASK AGAIN" }));
    act(() => { vi.advanceTimersByTime(CONFIRM_MS); });

    fireEvent.click(screen.getByRole("button", { name: "↺ ASK AGAIN" }));
    expect(onAskAgain).not.toHaveBeenCalled();
  });

  it("is shown on its own, without Retry or Dismiss", () => {
    /* A pending item has neither, and is where an answer that put a file in
     * the queue is most likely to be taken back. */
    show({ item: { ...ITEM, status: "pending" }, onAskAgain: () => {} });

    expect(screen.getByRole("button", { name: "↺ ASK AGAIN" })).toBeTruthy();
  });
});
