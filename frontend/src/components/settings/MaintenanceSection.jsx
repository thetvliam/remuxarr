import { useState, useEffect } from "react";
import { C } from "../../constants";

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
 ═ ═*═════════════════════════════════════════════════════════════════════════ */

/* ── Small reusable toggle row ──────────────────────────────────────────── */
const ToggleRow = ({ label, description, checked, onChange, disabled = false }) => (
  <div style={{
    display: "flex",
    alignItems: "flex-start",
    gap: 20,
    padding: "14px 0",
    borderBottom: `1px solid ${C.border}`,
  }}>
  <div style={{ flex: 1 }}>
  <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
  {label}
  </div>
  <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65 }}>
  {description}
  </div>
  </div>
  <button
  onClick={() => !disabled && onChange(!checked)}
  disabled={disabled}
  style={{
    flexShrink: 0,
    marginTop: 2,
    width: 40,
    height: 22,
    borderRadius: 11,
    border: `1px solid ${checked ? C.amber : C.border}`,
    background: checked ? C.amber + "33" : "transparent",
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
    borderRadius: "50%",
    background: checked ? C.amber : C.dim,
    transition: "left 0.15s, background 0.15s",
  }} />
  </button>
  </div>
);

/* ── Tag input for HH:MM times ──────────────────────────────────────────── */
const TimeTagInput = ({ value = [], onChange }) => {
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
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 8 }}>
      {value.map(t => (
        <span
        key={t}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          padding: "2px 8px",
          background: C.amber + "18",
          border: `1px solid ${C.amber}55`,
          color: C.amber,
          fontSize: 11,
          fontFamily: "inherit",
        }}
        >
        {t}
        <button
        onClick={() => remove(t)}
        style={{
          background: "none", border: "none",
          color: C.amber, cursor: "pointer",
          fontSize: 14, lineHeight: 1, padding: 0,
          fontFamily: "inherit",
        }}
        >×</button>
        </span>
      ))}
      </div>
    )}

    {/* Input row */}
    <div style={{ display: "flex", gap: 6 }}>
    <input
    value={draft}
    onChange={e => { setDraft(e.target.value); setError(""); }}
    onKeyDown={handleKeyDown}
    placeholder="14:30"
    maxLength={5}
    style={{
      width: 70,
      padding: "4px 8px",
      background: C.bg,
      border: `1px solid ${error ? C.red : C.border}`,
      color: C.text,
      fontSize: 12,
      fontFamily: "inherit",
      outline: "none",
    }}
    />
    <button
    onClick={add}
    style={{
      padding: "4px 10px",
      background: "transparent",
      border: `1px solid ${C.border}`,
      color: C.dim,
      fontSize: 10,
      fontFamily: "inherit",
      letterSpacing: "0.08em",
      cursor: "pointer",
    }}
    >ADD</button>
    </div>
    {error && (
      <div style={{ color: C.red, fontSize: 10, marginTop: 5 }}>{error}</div>
    )}
    </div>
  );
};

/* ── Main component ─────────────────────────────────────────────────────── */
export const MaintenanceSection = ({ api, toast }) => {
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
      if (!r.ok) toast?.("Failed to save setting", C.red);
    } catch (_) {
      toast?.("Failed to save setting", C.red);
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
          C.blue,
        );
      } else {
        toast?.("Cleanup failed", C.red);
      }
    } catch (_) {
      toast?.("Cleanup failed", C.red);
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
        toast?.("Force full rescan started — progress shown in the header", C.amber);
      } else if (r.status === 409) {
        toast?.("A scan is already in progress", C.red);
      } else {
        toast?.("Failed to start rescan", C.red);
      }
    } catch (_) {
      toast?.("Failed to start rescan", C.red);
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
        toast?.("Failed to check for orphaned files", C.red);
      }
    } catch (_) {
      toast?.("Failed to check for orphaned files", C.red);
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
          C.blue,
        );
        // Re-check rather than assume — reflects the real current state
        await checkOrphaned();
      } else {
        toast?.("Failed to remove orphaned files", C.red);
      }
    } catch (_) {
      toast?.("Failed to remove orphaned files", C.red);
    } finally {
      setOrphanedRemoving(false);
    }
  };

  const sectionLabel = (text) => (
    <div style={{
      color: C.amber,
      fontSize: 9,
      letterSpacing: "0.18em",
      fontWeight: 700,
      marginBottom: 4,
    }}>
    {text}
    </div>
  );

  return (
    <div style={{ marginTop: 36, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
    {sectionLabel("MAINTENANCE")}

    {/* ── Card 1: Scheduled Scans ─────────────────────────────────────── */}
    <div style={{
      padding: "16px",
      border: `1px solid ${C.border}`,
      marginBottom: 16,
    }}>
    <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 14 }}>
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
      padding: "14px 0",
      borderBottom: `1px solid ${C.border}`,
    }}>
    <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
    Scan Times
    </div>
    <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65, marginBottom: 10 }}>
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
      padding: "16px",
      border: `1px solid ${C.border}`,
    }}>
    <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 14 }}>
    Manual Cleanup
    </div>

    <div style={{ display: "flex", alignItems: "flex-start", gap: 20 }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65 }}>
    Scan the database for files that no longer exist on disk and remove
    their entries — including tracks, queue items, history, and forge
    records. Scoped to configured scan paths only.
    </div>
    {cleanupResult !== null && (
      <div style={{ color: C.blue, fontSize: 11, marginTop: 8 }}>
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
      padding: "6px 14px",
      background: "transparent",
      border: `1px solid ${cleanupRunning ? C.muted : C.blue}`,
      color: cleanupRunning ? C.muted : C.blue,
      fontSize: 10,
      fontFamily: "inherit",
      fontWeight: 700,
      letterSpacing: "0.1em",
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
      padding: "16px",
      border: `1px solid ${C.border}`,
      marginTop: 16,
    }}>
    <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 14 }}>
    Force Full Rescan
    </div>

    <div style={{ display: "flex", alignItems: "flex-start", gap: 20 }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65 }}>
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
      padding: "6px 14px",
      background: forceScanArmed ? C.amber + "22" : "transparent",
      border: `1px solid ${C.amber}`,
      color: C.amber,
      fontSize: 10,
      fontFamily: "inherit",
      fontWeight: 700,
      letterSpacing: "0.1em",
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
      padding: "16px",
      border: `1px solid ${C.border}`,
      marginTop: 16,
    }}>
    <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 14 }}>
    Orphaned Files
    </div>

    <div style={{ display: "flex", alignItems: "flex-start", gap: 20, marginBottom: orphanedChecked ? 14 : 0 }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65 }}>
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
      padding: "6px 14px",
      background: "transparent",
      border: `1px solid ${orphanedLoading ? C.muted : C.blue}`,
      color: orphanedLoading ? C.muted : C.blue,
      fontSize: 10,
      fontFamily: "inherit",
      fontWeight: 700,
      letterSpacing: "0.1em",
      cursor: orphanedLoading ? "not-allowed" : "pointer",
      whiteSpace: "nowrap",
    }}
    >
    {orphanedLoading ? "CHECKING…" : "CHECK FOR ORPHANED FILES"}
    </button>
    </div>

    {orphanedChecked && orphanedItems.length === 0 && (
      <div style={{ color: C.blue, fontSize: 11 }}>
      No orphaned entries found ✓
      </div>
    )}

    {orphanedChecked && orphanedItems.length > 0 && (
      <div>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "6px 12px",
        background: C.card,
        border: `1px solid ${C.border}`,
        borderBottom: "none",
      }}>
      <input type="checkbox" checked={allOrphanedSelected} onChange={toggleAllOrphaned} />
      <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.08em" }}>
      SELECT ALL ({orphanedItems.length})
      </span>
      </div>

      <div style={{ maxHeight: 280, overflowY: "auto", border: `1px solid ${C.border}` }}>
      {orphanedItems.map(item => (
        <div
        key={item.id}
        onClick={() => toggleOrphaned(item.id)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 12px",
          borderBottom: `1px solid ${C.border}`,
          cursor: "pointer",
          background: orphanedSelected.has(item.id) ? "#ffffff08" : "transparent",
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
          color: C.text, fontSize: 11,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
        {item.filename}
        </div>
        <div style={{
          color: C.muted, fontSize: 10,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
        {item.path}
        </div>
        </div>
        <span style={{
          flexShrink: 0,
          padding: "1px 6px",
          background: item.on_disk ? (C.blue + "18") : (C.dim + "18"),
                                  border: `1px solid ${item.on_disk ? C.blue : C.dim}44`,
                                  color: item.on_disk ? C.blue : C.dim,
                                  fontSize: 9,
                                  letterSpacing: "0.08em",
        }}>
        {item.on_disk ? "STILL ON DISK" : "FILE GONE"}
        </span>
        </div>
      ))}
      </div>

      <div style={{ marginTop: 10 }}>
      <button
      onClick={removeSelectedOrphaned}
      disabled={orphanedRemoving || orphanedSelected.size === 0}
      style={{
        padding: "6px 14px",
        background: orphanedRemoveArmed ? C.red + "22" : "transparent",
        border: `1px solid ${orphanedSelected.size === 0 ? C.muted : C.red}`,
        color: orphanedSelected.size === 0 ? C.muted : C.red,
        fontSize: 10,
        fontFamily: "inherit",
        fontWeight: 700,
        letterSpacing: "0.1em",
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
