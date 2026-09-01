/**
 * DangerZone — Clear Database.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The component took only { api, toast }, so it had no way to tell anything
 * that the data was gone, and POST /api/settings/clear-database broadcasts
 * nothing either. It is reached from Settings, where the dashboard panels are
 * unmounted; returning to the dashboard showed a queue still listing rows the
 * wipe had deleted, because that list lives in useAppData and survives the
 * page switch. Clicking one opened a detail fetch that 404'd, and dismissing
 * one addressed an id that no longer existed.
 *
 * The two-click confirmation is covered here as well, since firing the wipe
 * on a single click is the failure that cannot be undone.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DangerZone } from "../DangerZone";
import { ThemeProvider } from "../../../theme";
import { CONFIRM_MS } from "../../../constants";

const setup = (props = {}) => {
  const onCleared = vi.fn();
  const toast = vi.fn();
  render(
    <ThemeProvider>
      <DangerZone api="" toast={toast} onCleared={onCleared} {...props} />
    </ThemeProvider>,
  );
  return { onCleared, toast };
};

const button = () => screen.getByRole("button");

/** Click through the confirmation, so the request is actually sent. */
const confirmClear = async (user) => {
  await user.click(button());
  await user.click(button());
};

describe("DangerZone", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({}) })));
  });

  /* The arming window disarms itself so a button left armed cannot be fired
     by a stray click minutes later. Read from the shared constant rather than
     written as a number: the five confirm sites had drifted to 3s and 4s with
     nothing marking either as intended, which is what the constant exists to
     stop. A literal here would let them drift again while still passing.

     fireEvent rather than userEvent: userEvent schedules its own work on the
     timers these tests replace, so the two deadlock. */
  it("disarms itself once the confirmation window lapses", () => {
    vi.useFakeTimers();
    try {
      setup();
      fireEvent.click(button());
      expect(button()).toHaveTextContent(/CONFIRM/i);

      // Just short of the window: still armed.
      act(() => { vi.advanceTimersByTime(CONFIRM_MS - 100); });
      expect(button()).toHaveTextContent(/CONFIRM/i);

      act(() => { vi.advanceTimersByTime(200); });
      expect(button()).not.toHaveTextContent(/CONFIRM/i);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a click after the window lapses re-arms rather than firing", () => {
    vi.useFakeTimers();
    try {
      setup();
      fireEvent.click(button());
      act(() => { vi.advanceTimersByTime(CONFIRM_MS + 100); });

      fireEvent.click(button());

      expect(fetch).not.toHaveBeenCalled();
      expect(button()).toHaveTextContent(/CONFIRM/i);
    } finally {
      vi.useRealTimers();
    }
  });

  it("sends nothing on the first click", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(button());

    expect(fetch).not.toHaveBeenCalled();
    expect(button()).toHaveTextContent(/CONFIRM/i);
  });

  it("wipes on the second click", async () => {
    const user = userEvent.setup();
    setup();

    await confirmClear(user);

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(fetch.mock.calls[0][0])).toContain("/api/settings/clear-database");
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: "POST" });
  });

  it("tells its caller the data is gone", async () => {
    const user = userEvent.setup();
    const { onCleared } = setup();

    await confirmClear(user);

    expect(onCleared).toHaveBeenCalledTimes(1);
  });

  it("says nothing when the wipe was refused", async () => {
    // A failed clear deleted nothing, so refreshing every panel would be a
    // pointless round of fetches — and, worse, would suggest it worked.
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 })));
    const user = userEvent.setup();
    const { onCleared, toast } = setup();

    await confirmClear(user);

    expect(onCleared).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/failed/i), "error");
  });

  it("says nothing when the request never arrived", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const user = userEvent.setup();
    const { onCleared, toast } = setup();

    await confirmClear(user);

    expect(onCleared).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/failed/i), "error");
  });

  it("works without the callback, for a caller that does not pass one", async () => {
    const user = userEvent.setup();
    setup({ onCleared: undefined });

    await confirmClear(user);

    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
