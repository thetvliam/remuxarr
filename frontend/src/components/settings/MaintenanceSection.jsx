import { useState, useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * MAINTENANCE SECTION
 * The four cards of the Maintenance & Logs category, above LogViewer and
 * BuildInfoSection. This category has no schema-driven fields of its own, so
 * there is no SaveBar here — DangerZone is not below this, it is in the
 * separate Backup & Danger Zone category:
 *
 * 1. Scheduled Scans — enable/disable, configure HH:MM times, toggle
 *    whether automatic cleanup runs at the end of each scan.
 *
 * 2. Manual Cleanup — run the deleted-file cleanup on demand, shows
 *    how many DB entries were removed.
 *
 * 3. Force Full Rescan — clears the fingerprint cache so the next scan
 *    re-probes every file rather than skipping unchanged ones.
 *
 * 4. Orphaned Files — find and remove DB rows whose file is gone.
 *
 * Each toggle/tag saves immediately via PUT /api/settings/{key} so there's
 * no separate Save button needed. There is no PATCH route on that endpoint,
 * only GET and PUT.
 ═ * * ═*═════════════════════════════════════════════════════════════════════════ */

/* ── Small reusable toggle row ──────────────────────────────────────────── */
const ToggleRow = ({ label, description, checked, onChange, disabled = false }) => {
  const { palette, type, space, radius } = useTheme();
  return (
    <div style={{
      display: "flex",
      alignItems: "flex-start",
      gap: space.xxl,
      padding: `${space.xl}px 0`,
      borderBottom: `1px solid ${palette.border}`,
    }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xxs }}>
    {label}
    </div>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
    {description}
    </div>
    </div>
    <button
    // role + aria-checked is what conveys on/off here. The switch is drawn
    // entirely with a positioned knob, so without them it announced as an
    // unlabelled button with no state at all — the label sits in a sibling
    // div, so aria-label carries it across.
    role="switch"
    aria-checked={checked}
    aria-label={label}
    onClick={() => !disabled && onChange(!checked)}
    disabled={disabled}
    style={{
      flexShrink: 0,
      marginTop: space.hair,
      width: 40,
      height: 22,
      borderRadius: radius.pill,
      border: `1px solid ${checked ? palette.amber : palette.border}`,
      background: checked ? alpha(palette.amber, ALPHA.firm) : "transparent",
          cursor: disabled ? "not-allowed" : "pointer",
          position: "relative",
          transition: "border-color 0.15s, background 0.15s",
    }}
    >
    <span style={{
      position: "absolute",
      top: 2,
      left: checked ? 20 : 2,
      width: 16,
      height: 16,
      borderRadius: radius.full,
      background: checked ? palette.amber : palette.dim,
      transition: "left 0.15s, background 0.15s",
    }} />
    </button>
    </div>
  );
};

/* ── Tag input for HH:MM times ──────────────────────────────────────────── */
const TimeTagInput = ({ value = [], onChange }) => {
  const { palette, type, space, radius } = useTheme();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const isValidTime = (s) => /^([01]\d|2[0-3]):[0-5]\d$/.test(s.trim());

  const add = () => {
    const t = draft.trim();
    if (!t) return;
    if (!isValidTime(t)) {
      setError("Use HH:MM 24-hour format, e.g. 02:00 or 14:30");
      return;
    }
    if (value.includes(t)) {
      setError("That time is already in the list");
      return;
    }
    // Explicit comparator: the intent is chronological order, and a bare
    // .sort() only happens to produce it because isValidTime above enforces
    // zero-padded 24-hour HH:MM, where lexicographic and chronological
    // coincide. That is an invisible coupling between two functions — relax
    // the regex to allow "9:00", or move to 12-hour times, and the schedule
    // list silently misorders with nothing pointing back here.
    onChange([...value, t].sort((a, b) => a.localeCompare(b)));
    setDraft("");
    setError("");
  };

  const remove = (t) => onChange(value.filter(x => x !== t));

  const handleKeyDown = (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
    if (e.key === "Escape") { setDraft(""); setError(""); }
  };

  return (
    <div style={{ minWidth: 200, maxWidth: 300 }}>
    {/* Existing tags */}
    {value.length > 0 && (
      <div style={{ display: "flex", flexWrap: "wrap", gap: space.xs, marginBottom: space.sm }}>
      {value.map(t => (
        <span
        key={t}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: space.xs,
          padding: `${space.hair}px ${space.sm}px`,
          background: alpha(palette.amber, ALPHA.low),
                       border: `1px solid ${alpha(palette.amber, ALPHA.heavy)}`,
                       borderRadius: radius.sm,
                       color: palette.amber,
                       fontSize: type.size.md,
                       fontFamily: type.family,
        }}
        >
        {t}
        <button aria-label="Remove"
        onClick={() => remove(t)}
        style={{
          background: "none", border: "none",
          color: palette.amber, cursor: "pointer",
          fontSize: type.size.xl, lineHeight: type.leading.none, padding: 0,
          fontFamily: type.family,
        }}
        >×</button>
        </span>
      ))}
      </div>
    )}

    {/* Input row */}
    <div style={{ display: "flex", gap: space.xs }}>
    <input
    value={draft}
    onChange={e => { setDraft(e.target.value); setError(""); }}
    onKeyDown={handleKeyDown}
    placeholder="14:30"
    maxLength={5}
    style={{
      width: 70,
      padding: `${space.xxs}px ${space.sm}px`,
      background: palette.bg,
      border: `1px solid ${error ? palette.red : palette.border}`,
      borderRadius: radius.sm,
      color: palette.text,
      fontSize: type.size.base,
      fontFamily: type.family,
    }}
    />
    <button
    onClick={add}
    style={{
      padding: `${space.xxs}px ${space.md}px`,
      background: "transparent",
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      color: palette.dim,
      fontSize: type.size.sm,
      fontFamily: type.family,
      letterSpacing: type.tracking.normal,
      cursor: "pointer",
    }}
    >ADD</button>
    </div>
    {error && (
      <div style={{ color: palette.red, fontSize: type.size.sm, marginTop: space.xs }}>{error}</div>
    )}
    </div>
  );
};

/* ── Main component ─────────────────────────────────────────────────────── */
export const MaintenanceSection = ({ api, toast, reloadKey = 0 }) => {
  const { palette, type, space, radius, surface } = useTheme();
  const [settings, setSettings]         = useState({
    scheduled_scan_enabled: false,
    scheduled_scan_times:   [],
    auto_cleanup_on_scan:   true,
  });
  const [cleanupRunning, setCleanupRunning] = useState(false);
  const [cleanupResult,  setCleanupResult]  = useState(null); // null | number

  // Orphaned files — fetched on demand, not on mount, since this is a
  // rare-use maintenance check, not something that needs to stay live.
  const [orphanedChecked,  setOrphanedChecked]  = useState(false);
  const [orphanedLoading,  setOrphanedLoading]  = useState(false);
  const [orphanedItems,    setOrphanedItems]    = useState([]);
  const [orphanedSelected, setOrphanedSelected] = useState(new Set());
  const [orphanedRemoving, setOrphanedRemoving] = useState(false);
  const [orphanedRemoveArmed, setOrphanedRemoveArmed] = useState(false);
  useEffect(() => {
    if (!orphanedRemoveArmed) return;
    const t = setTimeout(() => setOrphanedRemoveArmed(false), 3000);
    return () => clearTimeout(t);
  }, [orphanedRemoveArmed]);

  // Two-click confirmation for Force Full Rescan — auto-disarms after 3 s
  const [forceScanArmed, setForceScanArmed] = useState(false);
  useEffect(() => {
    if (!forceScanArmed) return;
    const t = setTimeout(() => setForceScanArmed(false), 3000);
    return () => clearTimeout(t);
  }, [forceScanArmed]);

  // Load current values on mount
  useEffect(() => {
    Promise.all([
      fetch(`${api}/api/settings/scheduled_scan_enabled`).then(r => r.json()),
                fetch(`${api}/api/settings/scheduled_scan_times`).then(r => r.json()),
                fetch(`${api}/api/settings/auto_cleanup_on_scan`).then(r => r.json()),
    ])
    .then(([enabled, times, cleanup]) => {
      setSettings({
        scheduled_scan_enabled: !!enabled.value,
        scheduled_scan_times:   Array.isArray(times.value) ? times.value : [],
                  auto_cleanup_on_scan:   cleanup.value !== false,
      });
    })
    .catch(() => {});
    // reloadKey: bumped by SettingsPage after a settings import, which changes
    // these three keys server-side without this component knowing. Without it
    // the toggles kept rendering pre-import values indefinitely.
  }, [api, reloadKey]);

  /* Applied optimistically so the toggle responds instantly, and rolled
   * back if the write fails. Previously the toast reported the failure but
   * the switch stayed in its new position, so "Scheduled Scans: on" could
   * be displayed indefinitely while the server had it off — and the toast
   * is gone after five seconds, leaving no trace that the two disagree
   * until the next mount refetches. Reverting is what makes the toggle
   * mean what it shows. */
  const saveSetting = async (key, value) => {
    const previous = settings[key];
    setSettings((prev) => ({ ...prev, [key]: value }));
    const revert = () => setSettings((prev) => ({ ...prev, [key]: previous }));
    try {
      const r = await fetch(`${api}/api/settings/${key}`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ value }),
      });
      if (!r.ok) {
        revert();
        toast?.("Failed to save setting", "error");
      }
    } catch (err) {
      console.error("Save maintenance setting failed", err);
      revert();
      toast?.("Failed to save setting", "error");
    }
  };

  const runCleanup = async () => {
    setCleanupRunning(true);
    setCleanupResult(null);
    try {
      const r = await fetch(`${api}/api/scan/cleanup`, { method: "POST" });
      if (r.ok) {
        const data = await r.json();
        setCleanupResult(data.removed);
        // No success toast here. The backend broadcasts "cleanup_completed"
        // over the WebSocket and useAppData raises the toast from that, so
        // toasting the HTTP response too produced two near-identical messages
        // for every click ("removed N stale entries" here vs "N stale entries
        // removed" there — and character-for-character identical when the
        // count was zero).
        //
        // The WebSocket is the right one to keep: it also refreshes the
        // history and review panels, and it fires for a cleanup triggered
        // anywhere, not just from this button. The inline cleanupResult below
        // still gives immediate local feedback.
        //
        // The failure paths keep their toasts — a request that never reached
        // the backend produces no broadcast, so nothing else would report it.
      } else {
        toast?.("Cleanup failed", "error");
      }
    } catch (err) {
      console.error("Cleanup failed", err);
      toast?.("Cleanup failed", "error");
    } finally {
      setCleanupRunning(false);
    }
  };

  const runForceFullScan = async () => {
    if (!forceScanArmed) {
      setForceScanArmed(true);
      return;
    }
    setForceScanArmed(false);
    try {
      const r = await fetch(`${api}/api/scan/trigger`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ force_probe: true }),
      });
      if (r.ok) {
        toast?.("Force full rescan started — progress shown in the header", "notice");
      } else if (r.status === 409) {
        toast?.("A scan is already in progress", "error");
      } else {
        toast?.("Failed to start rescan", "error");
      }
    } catch (err) {
      console.error("Rescan request failed", err);
      toast?.("Failed to start rescan", "error");
    }
  };

  const checkOrphaned = async () => {
    setOrphanedLoading(true);
    setOrphanedChecked(false);
    setOrphanedSelected(new Set());
    try {
      const r = await fetch(`${api}/api/scan/orphaned`);
      if (r.ok) {
        const data = await r.json();
        setOrphanedItems(data.items || []);
        setOrphanedChecked(true);
      } else {
        toast?.("Failed to check for orphaned files", "error");
      }
    } catch (err) {
      console.error("Orphaned-file check failed", err);
      toast?.("Failed to check for orphaned files", "error");
    } finally {
      setOrphanedLoading(false);
    }
  };

  const toggleOrphaned = (id) => {
    setOrphanedSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allOrphanedSelected = orphanedItems.length > 0 &&
  orphanedItems.every(i => orphanedSelected.has(i.id));
  const toggleAllOrphaned = () => {
    setOrphanedSelected(allOrphanedSelected ? new Set() : new Set(orphanedItems.map(i => i.id)));
  };

  const removeSelectedOrphaned = async () => {
    if (orphanedSelected.size === 0) return;
    if (!orphanedRemoveArmed) {
      setOrphanedRemoveArmed(true);
      return;
    }
    setOrphanedRemoveArmed(false);
    setOrphanedRemoving(true);
    try {
      const r = await fetch(`${api}/api/scan/orphaned/remove`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ file_ids: Array.from(orphanedSelected) }),
      });
      if (r.ok) {
        const data = await r.json();
        toast?.(
          `Removed ${data.removed} orphaned ${data.removed === 1 ? "entry" : "entries"}`,
          "info",
        );
        // Re-check rather than assume — reflects the real current state
        await checkOrphaned();
      } else {
        toast?.("Failed to remove orphaned files", "error");
      }
    } catch (err) {
      console.error("Remove orphaned files failed", err);
      toast?.("Failed to remove orphaned files", "error");
    } finally {
      setOrphanedRemoving(false);
    }
  };

  const sectionLabel = (text) => (
    <div style={{
      color: palette.amber,
      fontSize: type.size.xs,
      letterSpacing: type.tracking.max,
      fontWeight: type.weight.bold,
      marginBottom: space.xxs,
    }}>
    {text}
    </div>
  );

  return (
    <div style={{ marginTop: space.giant, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
    {sectionLabel("MAINTENANCE")}

    {/* ── Card 1: Scheduled Scans ─────────────────────────────────────── */}
    <div style={{
      padding: space.xl,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      marginBottom: space.xl,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xl }}>
    Scheduled Scans
    </div>

    <ToggleRow
    label="Enable Scheduled Scans"
    description="Automatically run a library scan at the times configured below. Uses server local time — set the TZ environment variable on the container to match your timezone."
    checked={settings.scheduled_scan_enabled}
    onChange={v => saveSetting("scheduled_scan_enabled", v)}
    />

    {/* Scan times — always visible so times can be configured before enabling */}
    <div style={{
      padding: `${space.xl}px 0`,
      borderBottom: `1px solid ${palette.border}`,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xxs }}>
    Scan Times
    </div>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed, marginBottom: space.md }}>
    Times to run the scheduled scan each day, in 24-hour HH:MM format.
    Add as many as needed — e.g. 02:00 for 2 AM, 14:30 for 2:30 PM.
    </div>
    <TimeTagInput
    value={settings.scheduled_scan_times}
    onChange={v => saveSetting("scheduled_scan_times", v)}
    />
    </div>

    <ToggleRow
    label="Auto-cleanup on Scan"
    description="At the end of every scan, automatically remove database entries for files that no longer exist on disk. Files whose jobs are currently processing are skipped. You can also trigger this manually below."
    checked={settings.auto_cleanup_on_scan}
    onChange={v => saveSetting("auto_cleanup_on_scan", v)}
    />
    </div>

    {/* ── Card 2: Manual Cleanup ──────────────────────────────────────── */}
    <div style={{
      padding: space.xl,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xl }}>
    Manual Cleanup
    </div>

    <div style={{ display: "flex", alignItems: "flex-start", gap: space.xxl }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
    Scan the database for files that no longer exist on disk and remove
    their entries — including tracks, queue items, history, and forge
    records. Scoped to configured scan paths only.
    </div>
    {cleanupResult !== null && (
      <div style={{ color: palette.blue, fontSize: type.size.md, marginTop: space.sm }}>
      {cleanupResult === 0
        ? "No stale entries found."
        : `Removed ${cleanupResult} stale ${cleanupResult === 1 ? "entry" : "entries"}.`}
        </div>
    )}
    </div>

    <button
    onClick={runCleanup}
    disabled={cleanupRunning}
    style={{
      flexShrink: 0,
      padding: `${space.xs}px ${space.xl}px`,
      background: "transparent",
      border: `1px solid ${cleanupRunning ? palette.muted : palette.blue}`,
      borderRadius: radius.sm,
      color: cleanupRunning ? palette.muted : palette.blue,
      fontSize: type.size.sm,
      fontFamily: type.family,
      fontWeight: type.weight.bold,
      letterSpacing: type.tracking.wide,
      cursor: cleanupRunning ? "not-allowed" : "pointer",
      whiteSpace: "nowrap",
    }}
    >
    {cleanupRunning ? "RUNNING…" : "RUN CLEANUP NOW"}
    </button>
    </div>
    </div>

    {/* ── Card 3: Force Full Rescan ────────────────────────────────────── */}
    <div style={{
      padding: space.xl,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      marginTop: space.xl,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xl }}>
    Force Full Rescan
    </div>

    <div style={{ display: "flex", alignItems: "flex-start", gap: space.xxl }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
    Re-probes every file with ffprobe regardless of whether its size or
    modification time has changed. Database records are updated if the
    probe results differ from what is stored. Each file is then
    re-evaluated against the current settings — useful after changing
    audio, subtitle, or language preferences and wanting to apply them
    to files that were previously scanned and marked as unchanged.
    Files that need processing will be queued normally. Slower than a
    routine scan; not recommended for everyday use.
    </div>
    </div>

    <button
    onClick={runForceFullScan}
    style={{
      flexShrink: 0,
      padding: `${space.xs}px ${space.xl}px`,
      background: forceScanArmed ? alpha(palette.amber, ALPHA.medium) : "transparent",
          border: `1px solid ${palette.amber}`,
          borderRadius: radius.sm,
          color: palette.amber,
          fontSize: type.size.sm,
          fontFamily: type.family,
          fontWeight: type.weight.bold,
          letterSpacing: type.tracking.wide,
          cursor: "pointer",
          whiteSpace: "nowrap",
    }}
    >
    {forceScanArmed ? "CLICK AGAIN TO CONFIRM" : "FORCE FULL RESCAN"}
    </button>
    </div>
    </div>

    {/* ── Card 4: Orphaned Files ──────────────────────────────────────── */}
    <div style={{
      padding: space.xl,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      marginTop: space.xl,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xl }}>
    Orphaned Files
    </div>

    <div style={{ display: "flex", alignItems: "flex-start", gap: space.xxl, marginBottom: orphanedChecked ? 14 : 0 }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
    Manual Cleanup above only ever checks files inside your currently
    configured Media Library Paths, by design — it never touches
    anything outside them. If a path is ever removed from that list
    after files under it were scanned, those database entries become
    permanently invisible to Manual Cleanup, even if the files are
    long gone. This checks for exactly that — entries sitting outside
    every currently configured path — so they're visible instead of
    silently accumulating. Only removes the database entry; never
    touches anything on disk.
    </div>
    </div>

    <button
    onClick={checkOrphaned}
    disabled={orphanedLoading}
    style={{
      flexShrink: 0,
      padding: `${space.xs}px ${space.xl}px`,
      background: "transparent",
      border: `1px solid ${orphanedLoading ? palette.muted : palette.blue}`,
      borderRadius: radius.sm,
      color: orphanedLoading ? palette.muted : palette.blue,
      fontSize: type.size.sm,
      fontFamily: type.family,
      fontWeight: type.weight.bold,
      letterSpacing: type.tracking.wide,
      cursor: orphanedLoading ? "not-allowed" : "pointer",
      whiteSpace: "nowrap",
    }}
    >
    {orphanedLoading ? "CHECKING…" : "CHECK FOR ORPHANED FILES"}
    </button>
    </div>

    {orphanedChecked && orphanedItems.length === 0 && (
      <div style={{ color: palette.blue, fontSize: type.size.md }}>
      No orphaned entries found ✓
      </div>
    )}

    {orphanedChecked && orphanedItems.length > 0 && (
      <div>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: space.md,
        padding: `${space.xs}px ${space.lg}px`,
        background: palette.card,
        border: `1px solid ${palette.border}`,
        borderRadius: `${radius.sm}px ${radius.sm}px 0 0`,
        borderBottom: "none",
      }}>
      <input type="checkbox" checked={allOrphanedSelected} onChange={toggleAllOrphaned} />
      <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.normal }}>
      SELECT ALL ({orphanedItems.length})
      </span>
      </div>

      <div style={{ maxHeight: 280, overflowY: "auto", border: `1px solid ${palette.border}`, borderRadius: radius.sm }}>
      {orphanedItems.map(item => (
        <div
        key={item.id}
        onClick={() => toggleOrphaned(item.id)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: space.md,
          padding: `${space.sm}px ${space.lg}px`,
          borderBottom: `1px solid ${palette.border}`,
          cursor: "pointer",
          background: orphanedSelected.has(item.id) ? surface.rowSelectedBg : "transparent",
        }}
        >
        <input
        type="checkbox"
        checked={orphanedSelected.has(item.id)}
        onChange={() => toggleOrphaned(item.id)}
        onClick={e => e.stopPropagation()}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          color: palette.text, fontSize: type.size.md,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
        {item.filename}
        </div>
        <div style={{
          color: palette.muted, fontSize: type.size.sm,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
        {item.path}
        </div>
        </div>
        <span style={{
          flexShrink: 0,
          padding: `${space.hair}px ${space.xs}px`,
          background: item.on_disk ? (alpha(palette.blue, ALPHA.low)) : (alpha(palette.dim, ALPHA.low)),
                                  border: `1px solid ${alpha(item.on_disk ? palette.blue : palette.dim, ALPHA.strong)}`,
                                  borderRadius: radius.sm,
                                  color: item.on_disk ? palette.blue : palette.dim,
                                  fontSize: type.size.xs,
                                  letterSpacing: type.tracking.normal,
        }}>
        {item.on_disk ? "STILL ON DISK" : "FILE GONE"}
        </span>
        </div>
      ))}
      </div>

      <div style={{ marginTop: space.md }}>
      <button
      onClick={removeSelectedOrphaned}
      disabled={orphanedRemoving || orphanedSelected.size === 0}
      style={{
        padding: `${space.xs}px ${space.xl}px`,
        background: orphanedRemoveArmed ? alpha(palette.red, ALPHA.medium) : "transparent",
                                                     border: `1px solid ${orphanedSelected.size === 0 ? palette.muted : palette.red}`,
                                                     borderRadius: radius.sm,
                                                     color: orphanedSelected.size === 0 ? palette.muted : palette.red,
                                                     fontSize: type.size.sm,
                                                     fontFamily: type.family,
                                                     fontWeight: type.weight.bold,
                                                     letterSpacing: type.tracking.wide,
                                                     cursor: orphanedSelected.size === 0 ? "not-allowed" : "pointer",
      }}
      >
      {orphanedRemoving
        ? "REMOVING…"
        : orphanedRemoveArmed
        ? "CLICK AGAIN TO CONFIRM"
        : `REMOVE SELECTED (${orphanedSelected.size})`}
        </button>
        </div>
        </div>
    )}
    </div>
    </div>
  );
};
