import { useState, useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * MAINTENANCE SECTION
 * Two cards rendered below the main settings fields and above DangerZone:
 *
 * 1. Scheduled Scans — enable/disable, configure HH:MM times, toggle
 *    whether automatic cleanup runs at the end of each scan.
 *
 * 2. Manual Cleanup — run the deleted-file cleanup on demand, shows
 *    how many DB entries were removed.
 *
 * Each toggle/tag saves immediately via PATCH /api/settings/{key} so
 * there's no separate Save button needed (mirrors how DangerZone works).
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */

/* ── Small reusable toggle row ──────────────────────────────────────────── */
const ToggleRow = ({ label, description, checked, onChange, disabled = false }) => {
  const { palette, type, space, radius, legacy } = useTheme();
  return (
    <div style={{
      display: "flex",
      alignItems: "flex-start",
      gap: space.xxl,
      padding: `${legacy.settingRowPadY}px 0`,
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
  const { palette, type, space, legacy } = useTheme();
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
    onChange([...value, t].sort());
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
      <div style={{ display: "flex", flexWrap: "wrap", gap: legacy.tagGap, marginBottom: space.sm }}>
      {value.map(t => (
        <span
        key={t}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: legacy.tagGap,
          padding: `${space.hair}px ${space.sm}px`,
          background: alpha(palette.amber, ALPHA.low),
                       border: `1px solid ${alpha(palette.amber, ALPHA.heavy)}`,
                       color: palette.amber,
                       fontSize: type.size.md,
                       fontFamily: type.family,
        }}
        >
        {t}
        <button
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
      color: palette.text,
      fontSize: type.size.base,
      fontFamily: type.family,
      outline: "none",
    }}
    />
    <button
    onClick={add}
    style={{
      padding: `${space.xxs}px ${space.md}px`,
      background: "transparent",
      border: `1px solid ${palette.border}`,
      color: palette.dim,
      fontSize: type.size.sm,
      fontFamily: type.family,
      letterSpacing: type.tracking.normal,
      cursor: "pointer",
    }}
    >ADD</button>
    </div>
    {error && (
      <div style={{ color: palette.red, fontSize: type.size.sm, marginTop: legacy.labelGapY }}>{error}</div>
    )}
    </div>
  );
};

/* ── Main component ─────────────────────────────────────────────────────── */
export const MaintenanceSection = ({ api, toast }) => {
  const { palette, type, space, legacy } = useTheme();
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
  }, [api]);

  // Save a single setting immediately on change
  const saveSetting = async (key, value) => {
    setSettings(prev => ({ ...prev, [key]: value }));
    try {
      const r = await fetch(`${api}/api/settings/${key}`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ value }),
      });
      if (!r.ok) toast?.("Failed to save setting", palette.red);
    } catch (_) {
      toast?.("Failed to save setting", palette.red);
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
        toast?.(
          data.removed === 0
          ? "Cleanup complete — no stale entries found"
          : `Cleanup complete — removed ${data.removed} stale ${data.removed === 1 ? "entry" : "entries"}`,
          palette.blue,
        );
      } else {
        toast?.("Cleanup failed", palette.red);
      }
    } catch (_) {
      toast?.("Cleanup failed", palette.red);
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
        toast?.("Force full rescan started — progress shown in the header", palette.amber);
      } else if (r.status === 409) {
        toast?.("A scan is already in progress", palette.red);
      } else {
        toast?.("Failed to start rescan", palette.red);
      }
    } catch (_) {
      toast?.("Failed to start rescan", palette.red);
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
        toast?.("Failed to check for orphaned files", palette.red);
      }
    } catch (_) {
      toast?.("Failed to check for orphaned files", palette.red);
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
          palette.blue,
        );
        // Re-check rather than assume — reflects the real current state
        await checkOrphaned();
      } else {
        toast?.("Failed to remove orphaned files", palette.red);
      }
    } catch (_) {
      toast?.("Failed to remove orphaned files", palette.red);
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
    <div style={{ marginTop: legacy.sectionSepGapY, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
    {sectionLabel("MAINTENANCE")}

    {/* ── Card 1: Scheduled Scans ─────────────────────────────────────── */}
    <div style={{
      padding: space.xl,
      border: `1px solid ${palette.border}`,
      marginBottom: space.xl,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: legacy.descGapY }}>
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
      padding: `${legacy.settingRowPadY}px 0`,
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
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: legacy.descGapY }}>
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
      padding: `${space.xs}px ${legacy.actionPadX}px`,
      background: "transparent",
      border: `1px solid ${cleanupRunning ? palette.muted : palette.blue}`,
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
      marginTop: space.xl,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: legacy.descGapY }}>
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
      padding: `${space.xs}px ${legacy.actionPadX}px`,
      background: forceScanArmed ? alpha(palette.amber, ALPHA.medium) : "transparent",
          border: `1px solid ${palette.amber}`,
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
      marginTop: space.xl,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: legacy.descGapY }}>
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
      padding: `${space.xs}px ${legacy.actionPadX}px`,
      background: "transparent",
      border: `1px solid ${orphanedLoading ? palette.muted : palette.blue}`,
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
        borderBottom: "none",
      }}>
      <input type="checkbox" checked={allOrphanedSelected} onChange={toggleAllOrphaned} />
      <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.normal }}>
      SELECT ALL ({orphanedItems.length})
      </span>
      </div>

      <div style={{ maxHeight: 280, overflowY: "auto", border: `1px solid ${palette.border}` }}>
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
          background: orphanedSelected.has(item.id) ? legacy.rowSelectedBg : "transparent",
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
          padding: `${legacy.badgePadY}px ${legacy.badgePadX}px`,
          background: item.on_disk ? (alpha(palette.blue, ALPHA.low)) : (alpha(palette.dim, ALPHA.low)),
                                  border: `1px solid ${alpha(item.on_disk ? palette.blue : palette.dim, ALPHA.strong)}`,
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
        padding: `${space.xs}px ${legacy.actionPadX}px`,
        background: orphanedRemoveArmed ? alpha(palette.red, ALPHA.medium) : "transparent",
                                                     border: `1px solid ${orphanedSelected.size === 0 ? palette.muted : palette.red}`,
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
