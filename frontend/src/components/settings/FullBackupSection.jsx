import { useState, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";

const CONFIRM_PHRASE = "REPLACE DATABASE";

/* ── Full Database Backup & Restore ───────────────────────────────────────
 * Distinct from Backup & Restore above — this is the entire database
 * (every scanned file, track, queue item, history entry, Forge job),
 * not just settings. Import is genuinely destructive, so it needs a
 * typed confirmation phrase rather than the two-click pattern used
 * elsewhere — the stakes here are meaningfully higher than anything
 * else in this file. ──────────────────────────────────────────────────── */
export const FullBackupSection = ({ api, toast }) => {
  const { palette, type, space, radius } = useTheme();
  const [includeSecrets, setIncludeSecrets] = useState(true);
  const [pendingFile,    setPendingFile]    = useState(null);
  const [confirmText,    setConfirmText]    = useState("");
  const [importing,      setImporting]      = useState(false);
  const [restartNeeded,  setRestartNeeded]  = useState(null); // null | { backupPath }
  const fileInputRef = useRef(null);

  const handleExport = () => {
    const a = document.createElement("a");
    a.href = `${api}/api/backup/export?include_secrets=${includeSecrets}`;
    a.download = "remuxarr-backup.zip";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleFilePicked = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPendingFile(file);
    setConfirmText("");
  };

  const handleImport = async () => {
    if (!pendingFile || confirmText !== CONFIRM_PHRASE) return;

    setImporting(true);
    try {
      const body = new FormData();
      body.append("file", pendingFile);
      const r = await fetch(`${api}/api/backup/import`, { method: "POST", body });
      const data = await r.json();
      if (r.ok && data.success) {
        setRestartNeeded({ backupPath: data.previous_database_backup });
      } else {
        toast?.(data.detail || "Import failed", "error");
      }
    } catch (_) {
      toast?.("Import failed", "error");
    } finally {
      setImporting(false);
      setPendingFile(null);
      setConfirmText("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // Persistent, not a toast — this must not be missable or auto-dismiss
  // before the user actually restarts.
  if (restartNeeded) {
    return (
      <div style={{ marginTop: space.giant, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
      <div style={{
        padding: space.xxl,
        border: `1px solid ${palette.yellow}`,
        borderRadius: radius.sm,
        background: alpha(palette.yellow, ALPHA.soft),
      }}>
      <div style={{ color: palette.yellow, fontSize: type.size.base, fontWeight: type.weight.bold, marginBottom: space.sm }}>
      RESTART REQUIRED
      </div>
      <div style={{ color: palette.text, fontSize: type.size.base, lineHeight: type.leading.loose }}>
      The database has been replaced on disk, but this running instance
      is still using the old one — nothing changes here until you
      restart the container.
      </div>
      <div style={{ color: palette.muted, fontSize: type.size.md, marginTop: space.lg, lineHeight: type.leading.loose }}>
      Your previous database was saved to:
      <div style={{ color: palette.text, fontFamily: type.mono, marginTop: space.xxs, wordBreak: "break-all" }}>
      {restartNeeded.backupPath}
      </div>
      </div>
      </div>
      </div>
    );
  }

  const canImport = !!pendingFile && confirmText === CONFIRM_PHRASE;

  return (
    <div style={{ marginTop: space.giant, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
    <div style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold, marginBottom: space.xl }}>
    FULL DATABASE BACKUP &amp; RESTORE
    </div>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed, marginBottom: space.xxl }}>
    The entire database — every scanned file, track, queue item, history
    entry, and Forge job — not just settings. A restore on a different
    system assumes the same container-side media paths as the system it
    was exported from; if they don't match, use Orphaned Files above
    afterward to clean up anything that doesn't correspond to a real file.
    </div>

    {/* Export */}
    <div style={{ display: "flex", alignItems: "flex-start", gap: space.huge, padding: `${space.xl}px 0`, borderBottom: `1px solid ${palette.border}` }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xs }}>
    Export Full Backup
    </div>
    <label style={{ display: "flex", alignItems: "center", gap: space.sm, color: palette.muted, fontSize: type.size.md, cursor: "pointer" }}>
    <input
    type="checkbox"
    checked={includeSecrets}
    onChange={e => setIncludeSecrets(e.target.checked)}
    />
    Include connection secrets
    </label>
    </div>
    <button
    onClick={handleExport}
    style={{
      padding: `${space.xs}px ${space.xl}px`,
      background: "transparent",
      border: `1px solid ${palette.blue}`,
      borderRadius: radius.sm,
      color: palette.blue,
      fontSize: type.size.sm,
      fontFamily: type.family,
      fontWeight: type.weight.bold,
      letterSpacing: type.tracking.wide,
      cursor: "pointer",
      whiteSpace: "nowrap",
      flexShrink: 0,
    }}
    >
    EXPORT
    </button>
    </div>

    {/* Import */}
    <div style={{ padding: `${space.xl}px 0` }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xs }}>
    Import Full Backup
    </div>
    <div style={{ color: palette.red, fontSize: type.size.md, lineHeight: type.leading.relaxed, marginBottom: space.lg }}>
    Replaces this instance's entire database. The current database is
    backed up first, but everything currently here — scanned files,
    history, queue — will otherwise be gone. Requires a manual
    container restart to actually take effect.
    </div>

    <input
    ref={fileInputRef}
    type="file"
    accept=".zip"
    onChange={handleFilePicked}
    style={{
      display: "block",
      marginBottom: space.md,
      color: palette.muted,
      fontSize: type.size.md,
    }}
    />

    <div style={{ display: "flex", alignItems: "center", gap: space.md }}>
    <input
    type="text"
    value={confirmText}
    onChange={e => setConfirmText(e.target.value)}
    placeholder={`Type "${CONFIRM_PHRASE}" to confirm`}
    style={{
      flex: 1,
      maxWidth: 280,
      padding: `${space.xs}px ${space.md}px`,
      background: palette.bg,
      border: `1px solid ${confirmText === CONFIRM_PHRASE ? palette.red : palette.border}`,
      borderRadius: radius.sm,
      color: palette.text,
      fontFamily: type.family,
      fontSize: type.size.md,
    }}
    />
    <button
    onClick={handleImport}
    disabled={!canImport || importing}
    style={{
      padding: `${space.xs}px ${space.xl}px`,
      background: canImport ? alpha(palette.red, ALPHA.medium) : "transparent",
          border: `1px solid ${canImport ? palette.red : palette.muted}`,
          borderRadius: radius.sm,
          color: canImport ? palette.red : palette.muted,
          fontSize: type.size.sm,
          fontFamily: type.family,
          fontWeight: type.weight.bold,
          letterSpacing: type.tracking.wide,
          cursor: canImport && !importing ? "pointer" : "not-allowed",
          whiteSpace: "nowrap",
          flexShrink: 0,
    }}
    >
    {importing ? "IMPORTING…" : "REPLACE DATABASE"}
    </button>
    </div>
    </div>
    </div>
  );
};
