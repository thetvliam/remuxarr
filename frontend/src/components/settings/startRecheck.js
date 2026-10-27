/* ═══════════════════════════════════════════════════════════════════════════
 *  startRecheck
 *  Starts a re-check scan: files already scanned are decided again against
 *  the current settings, from the tracks stored for them, and only new or
 *  changed files are probed (scanner._process_file). Two places offer it, the
 *  Maintenance card and the row under the save bar after a save that changed
 *  one of those settings, so the request and what it tells the user live here
 *  once.
 *
 *  Returns true when the scan started. The scan's own end-of-scan toast,
 *  from the scan_completed message, reports what it found.
 ═══════════════════════════════════════════════════════════════════════════ */
export async function startRecheck(api, toast) {
  try {
    const r = await fetch(`${api}/api/scan/trigger`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ recheck: true }),
    });
    if (r.ok) {
      toast?.("Re-check started — progress shown in the header", "notice");
      return true;
    }
    toast?.(r.status === 409 ? "A scan is already in progress"
                             : "Failed to start the re-check", "error");
  } catch (err) {
    console.error("Re-check request failed", err);
    toast?.("Failed to start the re-check", "error");
  }
  return false;
}
