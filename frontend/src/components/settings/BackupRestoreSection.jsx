import { useState, useEffect, useRef } from "react";
import { C } from "../../constants";

/* ── Backup & Restore — settings export/import ───────────────────────────────
   Export is safe/read-only — no confirmation needed. Import overwrites
   current settings for whatever keys are present in the file, so it uses
   the same 4-second auto-cancel confirm pattern as DangerZone's destructive
   actions. Merge semantics, not replace: keys absent from the imported
   file (most notably secrets deliberately excluded at export time) are
   left completely untouched here. ──────────────────────────────────────── */
export const BackupRestoreSection = ({ api, toast }) => {
  const [includeSecrets, setIncludeSecrets] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [importing,  setImporting]  = useState(false);
  const fileInputRef = useRef(null);
  const pendingFileRef = useRef(null);

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), 4000);
    return () => clearTimeout(t);
  }, [confirming]);

  const handleExport = () => {
    const a = document.createElement("a");
    a.href = `${api}/api/settings/export?include_secrets=${includeSecrets}`;
    a.download = "remuxarr-settings.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleFilePicked = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    pendingFileRef.current = file;
    setConfirming(true);
  };

  const handleImportConfirmed = async () => {
    const file = pendingFileRef.current;
    if (!file) return;

    setImporting(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const r = await fetch(`${api}/api/settings/import`, { method: "POST", body });
      const data = await r.json();
      if (r.ok) {
        toast?.(
          `Imported ${data.applied} setting${data.applied === 1 ? "" : "s"}` +
          (data.skipped ? ` — ${data.skipped} unrecognized key${data.skipped === 1 ? "" : "s"} skipped` : ""),
          C.green,
        );
      } else {
        toast?.(data.detail || "Import failed", C.red);
      }
    } catch (_) {
      toast?.("Import failed", C.red);
    } finally {
      setImporting(false);
      setConfirming(false);
      pendingFileRef.current = null;
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div style={{ marginTop: 36, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
      <div style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700, marginBottom: 16 }}>
        BACKUP &amp; RESTORE
      </div>

      {/* Export */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 24, padding: "16px 0", borderBottom: `1px solid ${C.border}` }}>
        <div style={{ flex: 1 }}>
          <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 5 }}>
            Export Settings
          </div>
          <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65, marginBottom: 8 }}>
            Downloads your current configuration as a JSON file — useful for
            backing up before a change, or moving to a new system.
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, color: C.muted, fontSize: 11, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={includeSecrets}
              onChange={e => setIncludeSecrets(e.target.checked)}
            />
            Include connection secrets (Sonarr/Radarr API keys, Plex token, email password)
          </label>
          {includeSecrets && (
            <div style={{ color: C.yellow, fontSize: 10, marginTop: 4 }}>
              The exported file will contain live credentials — handle it like
              you would any file containing API keys.
            </div>
          )}
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
      <div style={{ display: "flex", alignItems: "flex-start", gap: 24, padding: "16px 0" }}>
        <div style={{ flex: 1 }}>
          <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 5 }}>
            Import Settings
          </div>
          <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65 }}>
            Applies settings from a previously exported file. Only keys
            actually present in the file are changed — anything not in it
            (e.g. secrets that were excluded at export time) is left exactly
            as it is now.
          </div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept="application/json"
          onChange={handleFilePicked}
          style={{ display: "none" }}
        />
        <button
          onClick={confirming ? handleImportConfirmed : () => fileInputRef.current?.click()}
          disabled={importing}
          style={{
            padding: "6px 14px",
            background: confirming ? C.red + "22" : "transparent",
            border: `1px solid ${C.red}`,
            color: C.red,
            fontSize: 10,
            fontFamily: "inherit",
            fontWeight: 700,
            letterSpacing: "0.1em",
            cursor: importing ? "not-allowed" : "pointer",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          {importing ? "IMPORTING…" : confirming ? "CLICK AGAIN TO CONFIRM" : "IMPORT…"}
        </button>
      </div>
    </div>
  );
};
