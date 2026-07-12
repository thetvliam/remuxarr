import { useState, useRef } from "react";
import { C } from "../../constants";

const CONFIRM_PHRASE = "REPLACE DATABASE";

/* ── Full Database Backup & Restore ───────────────────────────────────────
   Distinct from Backup & Restore above — this is the entire database
   (every scanned file, track, queue item, history entry, Forge job),
   not just settings. Import is genuinely destructive, so it needs a
   typed confirmation phrase rather than the two-click pattern used
   elsewhere — the stakes here are meaningfully higher than anything
   else in this file. ──────────────────────────────────────────────────── */
export const FullBackupSection = ({ api, toast }) => {
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
        toast?.(data.detail || "Import failed", C.red);
      }
    } catch (_) {
      toast?.("Import failed", C.red);
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
      <div style={{ marginTop: 36, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
        <div style={{
          padding: 20,
          border: `1px solid ${C.yellow}`,
          background: C.yellow + "14",
        }}>
          <div style={{ color: C.yellow, fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
            RESTART REQUIRED
          </div>
          <div style={{ color: C.text, fontSize: 12, lineHeight: 1.7 }}>
            The database has been replaced on disk, but this running instance
            is still using the old one — nothing changes here until you
            restart the container.
          </div>
          <div style={{ color: C.muted, fontSize: 11, marginTop: 12, lineHeight: 1.7 }}>
            Your previous database was saved to:
            <div style={{ color: C.text, fontFamily: "monospace", marginTop: 4, wordBreak: "break-all" }}>
              {restartNeeded.backupPath}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const canImport = !!pendingFile && confirmText === CONFIRM_PHRASE;

  return (
    <div style={{ marginTop: 36, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
      <div style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700, marginBottom: 16 }}>
        FULL DATABASE BACKUP &amp; RESTORE
      </div>
      <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65, marginBottom: 20 }}>
        The entire database — every scanned file, track, queue item, history
        entry, and Forge job — not just settings. A restore on a different
        system assumes the same container-side media paths as the system it
        was exported from; if they don't match, use Orphaned Files above
        afterward to clean up anything that doesn't correspond to a real file.
      </div>

      {/* Export */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 24, padding: "16px 0", borderBottom: `1px solid ${C.border}` }}>
        <div style={{ flex: 1 }}>
          <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 5 }}>
            Export Full Backup
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, color: C.muted, fontSize: 11, cursor: "pointer" }}>
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
            padding: "6px 14px",
            background: "transparent",
            border: `1px solid ${C.blue}`,
            color: C.blue,
            fontSize: 10,
            fontFamily: "inherit",
            fontWeight: 700,
            letterSpacing: "0.1em",
            cursor: "pointer",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          EXPORT
        </button>
      </div>

      {/* Import */}
      <div style={{ padding: "16px 0" }}>
        <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 5 }}>
          Import Full Backup
        </div>
        <div style={{ color: C.red, fontSize: 11, lineHeight: 1.65, marginBottom: 12 }}>
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
            marginBottom: 10,
            color: C.muted,
            fontSize: 11,
          }}
        />

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <input
            type="text"
            value={confirmText}
            onChange={e => setConfirmText(e.target.value)}
            placeholder={`Type "${CONFIRM_PHRASE}" to confirm`}
            style={{
              flex: 1,
              maxWidth: 280,
              padding: "6px 10px",
              background: C.bg,
              border: `1px solid ${confirmText === CONFIRM_PHRASE ? C.red : C.border}`,
              color: C.text,
              fontFamily: "inherit",
              fontSize: 11,
              outline: "none",
            }}
          />
          <button
            onClick={handleImport}
            disabled={!canImport || importing}
            style={{
              padding: "6px 14px",
              background: canImport ? C.red + "22" : "transparent",
              border: `1px solid ${canImport ? C.red : C.muted}`,
              color: canImport ? C.red : C.muted,
              fontSize: 10,
              fontFamily: "inherit",
              fontWeight: 700,
              letterSpacing: "0.1em",
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
