import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { IMPORT_CONFIRM_MS } from "../../constants";

/* ── Backup & Restore — settings export/import ───────────────────────────────
 * Export is safe/read-only — no confirmation needed. Import overwrites
 * current settings for whatever keys are present in the file, so it uses the
 * same two-click auto-cancel confirm pattern as DangerZone's destructive
 * actions — but on a 10-second window rather than DangerZone's 4, for the
 * reason given at CONFIRM_MS below. Merge semantics, not replace: keys absent from the imported
 * file (most notably secrets deliberately excluded at export time) are
 * left completely untouched here. ──────────────────────────────────────── */
export const BackupRestoreSection = ({ api, toast, onImported }) => {
  const { palette, type, space, radius } = useTheme();
  const [includeSecrets, setIncludeSecrets] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [importing,  setImporting]  = useState(false);
  const fileInputRef = useRef(null);
  const pendingFileRef = useRef(null);

  /* When the confirmation window lapses, the staged file and the input's
   * value are cleared too.
   *
   * Previously only `confirming` was reset. The file stayed in the ref and,
   * more importantly, fileInputRef.current.value stayed set — and a file
   * input fires no change event when you re-pick the file already in it. So
   * after letting the window lapse, clicking IMPORT… reopened the picker,
   * choosing the same file did nothing at all, and the button looked broken
   * until the user happened to pick a different one.
   *
   * IMPORT_CONFIRM_MS rather than the shared CONFIRM_MS: this asks the user
   * to read a filename and decide whether to overwrite every setting they
   * have, which is not a four-second decision. See constants.js for why that
   * is the only action given a longer window. */
  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => {
      setConfirming(false);
      pendingFileRef.current = null;
      if (fileInputRef.current) fileInputRef.current.value = "";
    }, IMPORT_CONFIRM_MS);
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
                "success",
        );
        // Not optional. SettingsPage loads schema and values once on mount, so
        // after an import it keeps rendering PRE-IMPORT values — and because
        // `baseline` is equally stale, isDirty stays false and nothing on
        // screen suggests the display is wrong.
        //
        // The serious part is save(): it PUTs every schema key from that stale
        // snapshot, so flipping any single unrelated toggle and pressing Save
        // silently overwrites the entire import with the pre-import values.
        // The toast said the import succeeded; the database then says
        // otherwise. Refetching here is what closes that window.
        await onImported?.();
      } else {
        toast?.(data.detail || "Import failed", "error");
      }
    } catch (err) {
      console.error("Settings import failed", err);
      toast?.("Import failed", "error");
    } finally {
      setImporting(false);
      setConfirming(false);
      pendingFileRef.current = null;
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div style={{ marginTop: space.giant, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
    <div style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold, marginBottom: space.xl }}>
    BACKUP &amp; RESTORE
    </div>

    {/* Export */}
    <div style={{ display: "flex", alignItems: "flex-start", gap: space.huge, padding: `${space.xl}px 0`, borderBottom: `1px solid ${palette.border}` }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xs }}>
    Export Settings
    </div>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed, marginBottom: space.sm }}>
    Downloads your current configuration as a JSON file — useful for
    backing up before a change, or moving to a new system.
    </div>
    <label style={{ display: "flex", alignItems: "center", gap: space.sm, color: palette.muted, fontSize: type.size.md, cursor: "pointer" }}>
    <input
    type="checkbox"
    checked={includeSecrets}
    onChange={e => setIncludeSecrets(e.target.checked)}
    />
    Include connection secrets (Sonarr/Radarr API keys, Plex token, email password)
    </label>
    {includeSecrets && (
      <div style={{ color: palette.yellow, fontSize: type.size.sm, marginTop: space.xxs }}>
      The exported file will contain live credentials — handle it like
      you would any file containing API keys.
      </div>
    )}
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
    <div style={{ display: "flex", alignItems: "flex-start", gap: space.huge, padding: `${space.xl}px 0` }}>
    <div style={{ flex: 1 }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xs }}>
    Import Settings
    </div>
    <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
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
      padding: `${space.xs}px ${space.xl}px`,
      background: confirming ? alpha(palette.red, ALPHA.medium) : "transparent",
          border: `1px solid ${palette.red}`,
          borderRadius: radius.sm,
          color: palette.red,
          fontSize: type.size.sm,
          fontFamily: type.family,
          fontWeight: type.weight.bold,
          letterSpacing: type.tracking.wide,
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
