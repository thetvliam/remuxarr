import { useState } from "react";
import { ThemeContext, useTheme, themes, alpha, ALPHA } from "../../theme";
import { themeToInputs, inputsToTheme } from "../../themeSource";

/* ═══════════════════════════════════════════════════════════════════════════
 * THEME EDITOR — developer tool, not a user feature
 *
 * Reached at #themes. Deliberately absent from AppHeader's NAV_ITEMS, so
 * nothing in the UI links to it.
 *
 * That is "not discoverable", which is weaker than "not reachable": the page
 * id is in useAppData's VALID_PAGES, so anyone who types the fragment gets
 * here. This was chosen knowingly over a build-time exclusion. What makes it
 * defensible is that the editor has no backend at all — saving is a download
 * the developer pastes into theme.jsx by hand, so there is no endpoint to
 * reach and nothing here can write to the server or the user's library. The
 * worst case is a confused user looking at a developer tool. If that stops
 * being acceptable, the change is an import.meta.env.DEV guard around the
 * VALID_PAGES entry and the branch in App.jsx, not a rewrite of this file.
 *
 * ── Why the preview needs no changes to any component ─────────────────────
 * The whole app reads its theme from ThemeContext, and theme.jsx exports
 * that context specifically so a caller can supply a theme object directly.
 * So the preview is a real page rendered inside a second provider holding
 * the draft. No component knows it is being previewed, nothing is mocked,
 * and there is no miniature to keep in step with the real thing the way
 * AppearanceSection's ThemePreview has to be.
 *
 * Verified rather than assumed: a real component rendered this way follows
 * the draft's colours, radii and spacing, including the actionCfg entries
 * regenerated from an edited tint.
 *
 * ── Why the controls do NOT render under the draft ────────────────────────
 * The controls read useTheme() and so follow whatever theme the user has
 * actually selected; only the preview pane gets the draft. This is not a
 * detail. Editing palette.bg to the same value as palette.text under a
 * single provider makes the editor itself unreadable, and the controls
 * needed to undo it are the ones that just disappeared. Splitting the two
 * means a draft can be as broken as you like and the way out stays visible.
 ═══════════════════════════════════════════════════════════════════════════ */

/* The palette keys, grouped the way they are reasoned about rather than in
 * object order. Structural colours set the page up; the named hues are what
 * a theme is usually recognised by, and they are also the inputs every
 * derived map is built from, so a change to one of them moves status
 * indicators, log levels, toasts and action badges at once. */
const PALETTE_GROUPS = [
  { label: "STRUCTURE", keys: ["bg", "card", "border", "text", "dim", "muted"] },
  { label: "HUES", keys: ["amber", "green", "red", "blue", "yellow", "violet", "cyan"] },
];

/* A hex colour the browser's colour input will accept. It only handles
 * 6-digit hex, and silently shows black for anything else — including the
 * 8-digit and 3-digit forms that are legal elsewhere in a theme. Palette
 * entries are all 6-digit today, but a theme is hand-editable, so the swatch
 * is driven through this and a value it cannot represent falls back to the
 * text field alone rather than being quietly rewritten to #000000. */
const HEX6 = /^#[0-9a-fA-F]{6}$/;

const ColourField = ({ name, value, onChange }) => {
  const { palette, type, space, radius } = useTheme();
  const usable = HEX6.test(value);
  /* Palette keys are unique within a theme, so this is unique on the page.
   *
   * A wrapping <label> around both inputs was the first shape and it was
   * wrong: a label may name only one control, so both announced as "bg" and
   * clicking the visible text focused whichever came first. htmlFor points
   * the visible name at the text field, and the swatch carries its own
   * aria-label, so the two are distinguishable to assistive technology and
   * to anything else querying by accessible name. */
  const fieldId = `theme-palette-${name}`;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: space.md,
      padding: `${space.xs}px 0`,
    }}>
      <label
        htmlFor={fieldId}
        style={{
          flex: "0 0 92px",
          color: palette.muted,
          fontSize: type.size.sm,
          fontFamily: type.family,
          letterSpacing: type.tracking.wide,
        }}
      >
        {name}
      </label>

      {/* The native swatch is the fast way to explore, the text field is the
        * only way to paste an exact value from a mockup. Both edit the same
        * state, so neither is authoritative over the other. */}
      <input
        type="color"
        aria-label={`${name} swatch`}
        value={usable ? value : "#000000"}
        disabled={!usable}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: 34, height: 24, padding: 0,
          background: "transparent",
          border: `1px solid ${palette.border}`,
          borderRadius: radius.sm,
          cursor: usable ? "pointer" : "not-allowed",
        }}
      />

      <input
        type="text"
        id={fieldId}
        value={value}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        style={{
          flex: 1, minWidth: 0,
          padding: `${space.xxs}px ${space.sm}px`,
          background: palette.bg,
          border: `1px solid ${usable ? palette.border : palette.red}`,
          borderRadius: radius.sm,
          color: palette.text,
          fontSize: type.size.sm,
          fontFamily: type.mono,
        }}
      />
    </div>
  );
};

export const ThemeEditorPage = ({ children, isMobile = false }) => {
  const { palette, type, space, radius, themeId } = useTheme();

  /* The draft starts as a copy of whichever theme is selected. themeToInputs
   * deep-copies, so editing the draft cannot reach back into the themes
   * singleton the rest of the app is rendering from. */
  const [baseId, setBaseId] = useState(themeId);
  const [inputs, setInputs] = useState(() => themeToInputs(themes[themeId]));

  const draft = inputsToTheme(inputs);

  const setPaletteKey = (key, value) =>
    setInputs((prev) => ({ ...prev, palette: { ...prev.palette, [key]: value } }));

  const resetTo = (id) => {
    setBaseId(id);
    setInputs(themeToInputs(themes[id]));
  };

  const heading = (text) => (
    <div style={{
      color: palette.amber,
      fontSize: type.size.xs,
      letterSpacing: type.tracking.max,
      fontWeight: type.weight.bold,
      paddingBottom: space.sm,
      borderBottom: `1px solid ${palette.border}`,
      marginBottom: space.lg,
    }}>
      {text}
    </div>
  );

  return (
    <div style={{
      flex: 1,
      display: "flex",
      flexDirection: isMobile ? "column" : "row",
      overflow: "hidden",
      background: palette.bg,
    }}>
      {/* ── Controls ───────────────────────────────────────────────────────
        * Rendered under the ACTIVE theme, never the draft. See the header. */}
      <div style={{
        flex: isMobile ? "none" : "0 0 340px",
        overflowY: "auto",
        padding: space.xl,
        borderRight: isMobile ? "none" : `1px solid ${palette.border}`,
        borderBottom: isMobile ? `1px solid ${palette.border}` : "none",
      }}>
        {heading("THEME EDITOR")}

        <div style={{
          color: palette.muted,
          fontSize: type.size.sm,
          lineHeight: type.leading.relaxed,
          marginBottom: space.xl,
        }}>
          Developer tool. Edits a draft in memory only — nothing is saved and
          the selected theme is untouched.
        </div>

        <div style={{ display: "flex", gap: space.sm, marginBottom: space.xl }}>
          {Object.values(themes).map((t) => {
            const on = t.id === baseId;
            return (
              <button
                key={t.id}
                onClick={() => resetTo(t.id)}
                aria-pressed={on}
                style={{
                  flex: 1,
                  padding: `${space.xs}px ${space.sm}px`,
                  background: on ? alpha(palette.amber, ALPHA.low) : "transparent",
                  border: `1px solid ${on ? palette.amber : palette.border}`,
                  borderRadius: radius.sm,
                  color: on ? palette.amber : palette.text,
                  fontSize: type.size.xs,
                  fontFamily: type.family,
                  fontWeight: type.weight.bold,
                  letterSpacing: type.tracking.wide,
                  cursor: "pointer",
                }}
              >
                {t.label.toUpperCase()}
              </button>
            );
          })}
        </div>

        {PALETTE_GROUPS.map((group) => (
          <div key={group.label} style={{ marginBottom: space.xl }}>
            <div style={{
              color: palette.dim,
              fontSize: type.size.xs,
              letterSpacing: type.tracking.widest,
              fontWeight: type.weight.bold,
              marginBottom: space.xs,
            }}>
              {group.label}
            </div>
            {group.keys.map((key) => (
              <ColourField
                key={key}
                name={key}
                value={inputs.palette[key]}
                onChange={(v) => setPaletteKey(key, v)}
              />
            ))}
          </div>
        ))}
      </div>

      {/* ── Preview ────────────────────────────────────────────────────────
        * A real page under the draft. The provider value carries themeId and
        * setThemeId alongside the theme because that is the shape useTheme()
        * consumers expect; setThemeId is inert here, since switching theme
        * from inside a preview of a draft would discard the draft. */}
      <div
        data-testid="theme-preview"
        style={{
          flex: 1,
          overflowY: "auto",
          background: draft.palette.bg,
          color: draft.palette.text,
        }}
      >
        <ThemeContext.Provider
          value={{ ...draft, themeId: draft.id, setThemeId: () => {} }}
        >
          {children}
        </ThemeContext.Provider>
      </div>
    </div>
  );
};
