import { useState } from "react";
import { useTheme } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * EXCLUDED FILES
 * Rendered by SettingsPage directly under the Exclude Patterns field.
 *
 * A scan's toast says how many files the exclude settings passed over and
 * nothing more, and Remuxarr keeps no record of them, so this asks
 * GET /api/scan/excluded, which walks the libraries again. On demand rather
 * than on mount, as the orphaned-files check is: it is a walk of every
 * library, and the settings page is opened far more often than this is
 * needed.
 *
 * The server reads the saved settings, not what is staged on this page, so
 * an edited pattern shows here only once it is saved. The text says so.
 ═══════════════════════════════════════════════════════════════════════════ */

export const ExcludedFilesSection = ({ api, toast }) => {
  const { palette, type, space, radius } = useTheme();
  const [loading, setLoading] = useState(false);
  const [items,   setItems]   = useState(null);   // null until a check succeeds

  const check = async () => {
    setLoading(true);
    setItems(null);
    try {
      const r = await fetch(`${api}/api/scan/excluded`);
      const data = await r.json().catch(() => null);
      if (r.ok) {
        setItems(data?.items || []);
      } else {
        // Refused with no library paths configured; the reason says what to
        // do, so it is shown rather than a generic failure.
        toast?.(data?.detail || "Failed to list excluded files", "error");
      }
    } catch (err) {
      console.error("Excluded-file check failed", err);
      toast?.("Failed to list excluded files", "error");
    } finally {
      setLoading(false);
    }
  };

  const ellipsis = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

  return (
    <div style={{
      padding: space.xl,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      margin: `${space.lg}px 0`,
    }}>
    <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.md }}>
    Excluded Files
    </div>

    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: space.xl }}>
    <div style={{ flex: "1 1 240px", color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
    The files your saved exclude settings leave alone, and what matched
    each one. Worked out when you press the button, from the settings as
    they are saved, so it can differ from the count your last scan
    reported if you have changed them since.
    </div>
    <button
    onClick={check}
    disabled={loading}
    style={{
      flexShrink: 0,
      padding: `${space.xs}px ${space.xl}px`,
      background: "transparent",
      border: `1px solid ${loading ? palette.muted : palette.blue}`,
      borderRadius: radius.sm,
      color: loading ? palette.muted : palette.blue,
      fontSize: type.size.sm,
      fontFamily: type.family,
      fontWeight: type.weight.bold,
      letterSpacing: type.tracking.wide,
      cursor: loading ? "not-allowed" : "pointer",
      whiteSpace: "nowrap",
    }}
    >
    {loading ? "CHECKING…" : "SHOW EXCLUDED FILES"}
    </button>
    </div>

    {items && items.length === 0 && (
      <div style={{ color: palette.blue, fontSize: type.size.md, marginTop: space.lg }}>
      No files are excluded.
      </div>
    )}

    {items && items.length > 0 && (
      <div style={{ marginTop: space.lg }}>
      <div style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.normal, marginBottom: space.xs }}>
      {items.length} EXCLUDED {items.length === 1 ? "FILE" : "FILES"}
      </div>
      <ul style={{
        listStyle: "none", margin: 0, padding: 0,
        maxHeight: 280, overflowY: "auto",
        border: `1px solid ${palette.border}`, borderRadius: radius.sm,
      }}>
      {items.map(item => (
        <li key={item.path} style={{
          padding: `${space.sm}px ${space.lg}px`,
          borderBottom: `1px solid ${palette.border}`,
          minWidth: 0,
        }}>
        <div style={{ color: palette.text, fontSize: type.size.md, ...ellipsis }}>
        {item.filename}
        </div>
        <div style={{ color: palette.amber, fontSize: type.size.sm }}>
        {item.reason}
        </div>
        <div title={item.path} style={{ color: palette.muted, fontSize: type.size.sm, ...ellipsis }}>
        {item.path}
        </div>
        </li>
      ))}
      </ul>
      </div>
    )}
    </div>
  );
};
