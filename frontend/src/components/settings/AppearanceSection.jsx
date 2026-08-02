import { useTheme, themes, alpha, ALPHA } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * APPEARANCE
 *
 * Theme picker. Unlike every other section on this page, nothing here is a
 * backend setting: the choice is per-browser and ThemeProvider persists it to
 * localStorage. So it deliberately does NOT join the dirty/save flow — there
 * is nothing to save, and showing a Save bar for it would imply the change is
 * pending when it has already applied.
 ═══════════════════════════════════════════════════════════════════════════ */

/* A miniature of the app chrome, rendered from a GIVEN theme's tokens rather
 * than the active one. This is the one place in the codebase that reads a
 * theme object directly instead of calling useTheme(), and it has to: the
 * whole point is to show what a theme you are NOT currently using looks like.
 *
 * It previews structure as well as colour — padding, type size, tracking and
 * radius all come from the previewed theme — because that is the difference
 * a swatch strip alone would hide. */
const ThemePreview = ({ t }) => (
  <div style={{
    background: t.palette.card,
    border: `1px solid ${t.palette.border}`,
    borderRadius: t.radius.sm,
    overflow: "hidden",
    flexShrink: 0,
    width: 200,
  }}>
    {/* panel header */}
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: t.space.sm,
      padding: `${t.space.sm}px ${t.space.xl}px`,
      borderBottom: `1px solid ${t.palette.border}`,
    }}>
      <span style={{
        color: t.palette.amber,
        fontSize: t.type.size.xs,
        fontWeight: t.type.weight.bold,
        letterSpacing: t.type.tracking.max,
        fontFamily: t.type.root,
      }}>QUEUE</span>
    </div>

    {/* two rows */}
    {[t.palette.green, t.palette.blue].map((c, i) => (
      <div key={i} style={{
        display: "flex",
        alignItems: "center",
        gap: t.space.md,
        padding: `${t.space.md}px ${t.space.xl}px`,
        borderBottom: i === 0 ? `1px solid ${t.palette.border}` : "none",
      }}>
        <span style={{
          width: t.size.ledSizeSm, height: t.size.ledSizeSm,
          borderRadius: t.radius.full, background: c, flexShrink: 0,
        }} />
        <span style={{
          flex: 1, minWidth: 0, overflow: "hidden",
          textOverflow: "ellipsis", whiteSpace: "nowrap",
          color: t.palette.text,
          fontSize: t.type.size.sm,
          fontFamily: t.type.root,
        }}>Show.S01E0{i + 1}.mkv</span>
        <span style={{
          padding: `${t.space.hair}px ${t.space.xs}px`,
          background: alpha(t.palette.amber, ALPHA.low),
          border: `1px solid ${alpha(t.palette.amber, ALPHA.strong)}`,
          borderRadius: t.radius.sm,
          color: t.palette.amber,
          fontSize: t.type.size.xs,
          letterSpacing: t.type.tracking.wide,
          fontFamily: t.type.root,
        }}>AC3</span>
      </div>
    ))}
  </div>
);

export const AppearanceSection = () => {
  const { palette, type, space, radius, themeId, setThemeId } = useTheme();
  const list = Object.values(themes);

  return (
    <div>
      <div style={{
        color: palette.amber,
        fontSize: type.size.xs,
        letterSpacing: type.tracking.max,
        fontWeight: type.weight.bold,
        margin: `${space.xxs}px 0 0`,
        paddingBottom: space.sm,
        borderBottom: `1px solid ${palette.border}`,
      }}>
        THEME
      </div>

      <div style={{
        color: palette.muted,
        fontSize: type.size.md,
        lineHeight: type.leading.relaxed,
        margin: `${space.xl}px 0 ${space.xl}px`,
      }}>
        Applies immediately and is remembered in this browser only — it is not
        a server setting and does not need saving.
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: space.lg }}>
        {list.map(t => {
          const on = t.id === themeId;
          return (
            <button
              key={t.id}
              onClick={() => setThemeId(t.id)}
              aria-pressed={on}
              style={{
                display: "flex",
                alignItems: "center",
                gap: space.huge,
                width: "100%",
                textAlign: "left",
                padding: space.xl,
                background: on ? alpha(palette.amber, ALPHA.low) : "transparent",
                border: `1px solid ${on ? palette.amber : palette.border}`,
                borderRadius: radius.sm,
                cursor: "pointer",
                fontFamily: type.family,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  display: "flex", alignItems: "center", gap: space.sm,
                  marginBottom: space.xs,
                }}>
                  <span style={{
                    color: on ? palette.amber : palette.text,
                    fontSize: type.size.base,
                    fontWeight: type.weight.bold,
                    letterSpacing: type.tracking.wide,
                  }}>
                    {t.label.toUpperCase()}
                  </span>
                  {on && (
                    <span style={{
                      color: palette.amber,
                      fontSize: type.size.xs,
                      letterSpacing: type.tracking.wide,
                    }}>
                      ACTIVE
                    </span>
                  )}
                </div>
                <div style={{
                  color: palette.muted,
                  fontSize: type.size.sm,
                  lineHeight: type.leading.relaxed,
                }}>
                  {t.blurb}
                </div>
              </div>

              <ThemePreview t={t} />
            </button>
          );
        })}
      </div>
    </div>
  );
};
