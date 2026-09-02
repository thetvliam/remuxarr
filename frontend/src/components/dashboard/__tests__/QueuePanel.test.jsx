/**
 * QueuePanel — the CLEAR QUEUE control.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The panel had no tests. Its visibility rule was written as a filter on
 * item.status, which cannot ever exclude anything: /api/queue/ returns only
 * pending and processing rows, and useAppData's pendingQueue strips the
 * processing ones before this panel is rendered. Rewriting it as items.length
 * is meant to be exactly equivalent, and replacing the count with a constant
 * zero broke nothing in the whole suite — so the rule was pinned here before
 * the line was simplified, rather than trusting that reading of two other
 * files.
 *
 * The two-click confirmation is covered for the same reason DangerZone covers
 * its own: clearing the queue on a single stray click is not undoable from
 * the UI.
 */
import { fireEvent, render, screen, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QueuePanel } from "../QueuePanel";
import { ThemeProvider } from "../../../theme";
import { CONFIRM_MS } from "../../../constants";

const pending = (id) => ({
  id, status: "pending", file: { filename: `f${id}.mkv` }, reason: "remux",
});

const setup = (items) => {
  const onClear = vi.fn();
  render(
    <ThemeProvider>
      <QueuePanel items={items} onClear={onClear}
                  onSelect={vi.fn()} onDismiss={vi.fn()} onPrioritize={vi.fn()} />
    </ThemeProvider>,
  );
  return { onClear };
};

const clearButton = () => screen.queryByTitle(/confirm|Remove all pending items/i);

describe("QueuePanel — CLEAR QUEUE", () => {
  it("is offered when the queue has items", () => {
    setup([pending(1), pending(2)]);
    expect(clearButton()).toBeTruthy();
  });

  it("is not offered when the queue is empty", () => {
    setup([]);
    expect(clearButton()).toBeNull();
  });

  it("needs a second click before it clears", () => {
    const { onClear } = setup([pending(1)]);

    fireEvent.click(clearButton());

    expect(onClear).not.toHaveBeenCalled();
    expect(clearButton()).toHaveAttribute("title", expect.stringMatching(/confirm/i));

    fireEvent.click(clearButton());
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("disarms itself once the confirmation window lapses", () => {
    /* Read from the shared constant rather than written as a number: this
       site's own comment claimed 3 seconds while the code used CONFIRM_MS,
       which is the drift the constant exists to stop. */
    vi.useFakeTimers();
    try {
      const { onClear } = setup([pending(1)]);

      fireEvent.click(clearButton());
      expect(clearButton()).toHaveAttribute("title", expect.stringMatching(/confirm/i));

      act(() => { vi.advanceTimersByTime(CONFIRM_MS - 100); });
      expect(clearButton()).toHaveAttribute("title", expect.stringMatching(/confirm/i));

      act(() => { vi.advanceTimersByTime(200); });
      expect(clearButton()).not.toHaveAttribute("title", expect.stringMatching(/confirm/i));

      // A lapsed arming must not leave the next click firing straight through.
      fireEvent.click(clearButton());
      expect(onClear).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
