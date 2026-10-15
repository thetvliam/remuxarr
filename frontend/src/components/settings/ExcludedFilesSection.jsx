import { useState } from "react";
import { useTheme } from "../../theme";
import { Btn } from "../atoms/Btn";

/* ═══════════════════════════════════════════════════════════════════════════
 * EXCLUDED FILES
 * Rendered by SettingsPage directly under the Always Include field, which
 * follows Exclude Patterns: the two settings that decide this list.
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
 *
 * Each row's Include button hands the server's include_as entry to
 * onInclude, which stages it in the Always Include box like any other edit:
 * nothing is written until SAVE CHANGES. `included` is that box's staged
 * list, so a row already in it shows as added rather than offering the
 * button again, which is also what stops a second click adding it twice.
 ═══════════════════════════════════════════════════════════════════════════ */

export const ExcludedFilesSection = ({ api, toast, included = [], onInclude }) => {
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
    reported if you have changed them since. Include adds a file to Always
    Include above, which takes effect when you save.
    </div>
    <Btn
    size="md"
    label={loading ? "CHECKING…" : "SHOW EXCLUDED FILES"}
    color={loading ? palette.muted : palette.blue}
    onClick={check}
    disabled={loading}
    />
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
      {items.map(item => {
        const added = included.includes(item.include_as);
        return (
          <li key={item.path} style={{
            display: "flex", alignItems: "center", gap: space.md,
            padding: `${space.sm}px ${space.lg}px`,
            borderBottom: `1px solid ${palette.border}`,
          }}>
          <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ color: palette.text, fontSize: type.size.md, ...ellipsis }}>
          {item.filename}
          </div>
          <div style={{ color: palette.amber, fontSize: type.size.sm }}>
          {item.reason}
          </div>
          <div title={item.path} style={{ color: palette.muted, fontSize: type.size.sm, ...ellipsis }}>
          {item.path}
          </div>
          </div>
          {added ? (
            <span style={{
              flexShrink: 0, color: palette.dim,
              fontSize: type.size.xs, letterSpacing: type.tracking.normal,
            }}>
            ADDED
            </span>
          ) : (
            <button
            onClick={() => onInclude?.(item.include_as)}
            aria-label={`Include ${item.filename}`}
            title={`Add ${item.include_as} to Always Include`}
            style={{
              flexShrink: 0,
              padding: `${space.hair}px ${space.md}px`,
              background: "transparent",
              border: `1px solid ${palette.blue}`,
              borderRadius: radius.sm,
              color: palette.blue,
              fontSize: type.size.xs,
              fontFamily: type.family,
              fontWeight: type.weight.bold,
              letterSpacing: type.tracking.wide,
              cursor: "pointer",
            }}
            >
            INCLUDE
            </button>
          )}
          </li>
        );
      })}
      </ul>
      </div>
    )}
    </div>
  );
};
