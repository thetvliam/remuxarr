/**
 * useArmedConfirm — the two-click confirm's window.
 *
 * Six buttons wrote this out by hand: Clear Database, the recycle bin's
 * permanent deletes, Clear Queue, orphaned-entry removal, Force Full Rescan
 * and the settings import. Each keeps its own test that its window lapses,
 * which is what shows the move to this hook changed none of them; these pin
 * the hook's own contract, including the two things only the settings import
 * uses (its own window, and onLapse), which no other site would notice
 * breaking.
 *
 * The windows are read from the constants rather than written as numbers,
 * as the site tests do, so the two cannot drift apart unnoticed.
 *
 * Verified by mutation. Five mutants of the hook were run against the suite
 * without this file: the window never lapsing, a custom window ignored,
 * onLapse never called, onLapse also called on disarm(), and the timer's
 * cleanup removed. Three were already caught by the site tests and are not
 * claimed: the window never lapsing, the custom window ignored, and the
 * cleanup removed (by the settings import's test that an earlier timer
 * cannot disarm a fresh confirmation). Both onLapse mutants survived it, and
 * all five are killed here.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useArmedConfirm } from "../useArmedConfirm";
import { CONFIRM_MS, IMPORT_CONFIRM_MS } from "../../constants";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

const wait = (ms) => act(() => { vi.advanceTimersByTime(ms); });

describe("useArmedConfirm", () => {
  it("starts disarmed, and arms", () => {
    const { result } = renderHook(() => useArmedConfirm());
    expect(result.current.armed).toBe(false);

    act(() => { result.current.arm(); });
    expect(result.current.armed).toBe(true);
  });

  it("disarms itself when CONFIRM_MS runs out, and not before", () => {
    const { result } = renderHook(() => useArmedConfirm());
    act(() => { result.current.arm(); });

    wait(CONFIRM_MS - 1);
    expect(result.current.armed).toBe(true);
    wait(1);
    expect(result.current.armed).toBe(false);
  });

  it("keeps a window of its own when given one", () => {
    const { result } = renderHook(() => useArmedConfirm({ ms: IMPORT_CONFIRM_MS }));
    act(() => { result.current.arm(); });

    wait(IMPORT_CONFIRM_MS - 1);
    expect(result.current.armed).toBe(true);
    wait(1);
    expect(result.current.armed).toBe(false);
  });

  it("disarms at once when told to", () => {
    const { result } = renderHook(() => useArmedConfirm());
    act(() => { result.current.arm(); });

    act(() => { result.current.disarm(); });
    expect(result.current.armed).toBe(false);
  });

  it("calls onLapse when the window runs out, and not when disarmed", () => {
    const onLapse = vi.fn();
    const { result } = renderHook(() => useArmedConfirm({ onLapse }));

    act(() => { result.current.arm(); });
    act(() => { result.current.disarm(); });
    wait(CONFIRM_MS);
    expect(onLapse).not.toHaveBeenCalled();

    act(() => { result.current.arm(); });
    wait(CONFIRM_MS);
    expect(onLapse).toHaveBeenCalledTimes(1);
  });

  it("gives a fresh window after a disarm, which the old timer cannot cut short", () => {
    /* Arm, disarm, and arm again just before the first window would have
     * run out. A timer that outlived its window would disarm the second one
     * almost at once. */
    const { result } = renderHook(() => useArmedConfirm());
    act(() => { result.current.arm(); });
    wait(CONFIRM_MS - 100);
    act(() => { result.current.disarm(); });
    act(() => { result.current.arm(); });

    wait(200);
    expect(result.current.armed).toBe(true);
    wait(CONFIRM_MS - 200);
    expect(result.current.armed).toBe(false);
  });
});
