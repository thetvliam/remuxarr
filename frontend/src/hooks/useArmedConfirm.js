import { useCallback, useEffect, useRef, useState } from "react";

import { CONFIRM_MS } from "../constants";

/* ═══════════════════════════════════════════════════════════════════════════
 *  useArmedConfirm
 *  The state behind a two-click destructive button: the first click arms it,
 *  a second within the window does the thing, and if the window runs out it
 *  disarms itself. Returns { armed, arm, disarm }.
 *
 *  Six buttons wrote this out by hand: Clear Database, the recycle bin's
 *  permanent deletes, Clear Queue, orphaned-entry removal, Force Full Rescan
 *  and the settings import. Five were the same lines, and they had already
 *  drifted once, to 3s in two places and 4s in two others (see CONFIRM_MS).
 *  The rule now lives here.
 *
 *  The timer is keyed on `armed`, with a cleanup, rather than started in the
 *  click handler. A timer started per click stacked, and each one outlived
 *  the click that set it: arm, disarm and re-arm, and the first click's timer
 *  cut the new window short. With the cleanup there is only ever the one
 *  timer for the current window, and none after unmount.
 *
 *  ms       the window; CONFIRM_MS unless the action has its own reason (the
 *           settings import uses IMPORT_CONFIRM_MS, see constants.js).
 *  onLapse  called when the window runs out, and only then: not on disarm(),
 *           which is the caller acting, nor on unmount. The settings import
 *           uses it to forget the file it was about to apply.
 ═══════════════════════════════════════════════════════════════════════════ */
export function useArmedConfirm({ ms = CONFIRM_MS, onLapse } = {}) {
  const [armed, setArmed] = useState(false);

  // Read at the moment the window runs out, so a caller passing a fresh
  // function each render neither restarts the timer nor calls a stale one.
  const onLapseRef = useRef(onLapse);
  onLapseRef.current = onLapse;

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => {
      setArmed(false);
      onLapseRef.current?.();
    }, ms);
    return () => clearTimeout(t);
  }, [armed, ms]);

  const arm    = useCallback(() => setArmed(true), []);
  const disarm = useCallback(() => setArmed(false), []);

  return { armed, arm, disarm };
}
