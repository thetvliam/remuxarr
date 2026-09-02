/* ═══════════════════════════════════════════════════════════════════════════
 * THEME SOURCE — the round trip between a theme object and its source text
 *
 * theme.jsx defines each theme as a hand-written literal. This module goes
 * the other way: it recovers the values a person actually chose from one of
 * those literals, rebuilds a live theme object from a set of chosen values,
 * and emits the source text for a theme block that can go back into
 * theme.jsx.
 *
 * ── Inputs, not outputs ───────────────────────────────────────────────────
 * A theme object carries 155 leaf values, and 52 of them are not choices:
 * statusColor (11), levelColor (5), toastTone (8) and actionCfg (28) are
 * built from the palette by the four functions in theme.jsx. The remaining
 * 103, plus the 14-value tint that never appears in the object at all, are
 * the 117 real inputs.
 *
 * So this module edits the INPUTS and regenerates the rest. The alternative
 * — treating a theme as a flat bag of 155 values and writing them all back
 * out — loses the tint (it is an argument to buildActionCfg, not a stored
 * key), and lets the derived maps be edited into states no palette could
 * produce, so a theme's success colour and its toast success tone could
 * quietly stop matching.
 *
 * Nothing in theme.jsx was restructured to make this work. The tint is
 * recoverable from the built theme as it stands (see recoverTint below), so
 * the shipped literals are untouched — which is what makes the
 * round-trip test meaningful. If those literals had been rewritten to go
 * through this module, the test would be comparing this code against itself
 * rather than against the themes the app actually ships.
 *
 * ── What this module does not do ──────────────────────────────────────────
 * themeToSource emits one theme block, matching the "copy a block below"
 * shape that theme.jsx's own header documents. It does not insert that block
 * into the file and it does not touch the `themes` export — deciding where a
 * saved theme goes is a separate problem with its own constraints, and this
 * module is the part of it that is the same whichever way that goes.
 *
 * ── What it refuses ───────────────────────────────────────────────────────
 * Two checks, and the second exists because the first is not enough. Every
 * leaf has to be a string or a finite number, and the whole theme has to
 * have the same shape as the shipped one — same key paths, same depth. A
 * palette entry written as { hex: "#fff" } passes the leaf check, since the
 * string underneath it is a perfectly good string, and still breaks the
 * theme. Both throw. Nothing here coerces, because a serialiser that
 * quietly wrote a bad value would turn a one-off mistake into a repeatable
 * one, and the mistake it would be repeating renders as nothing at all.
 ═══════════════════════════════════════════════════════════════════════════ */

import {
  themes,
  DEFAULT_THEME_ID,
  buildStatusColor,
  buildLevelColor,
  buildToastTone,
  buildActionCfg,
} from "./theme";

/* The theme object's top-level keys, in the order the literals declare them.
 *
 * Hardcoded rather than derived from themes.terminal at runtime, which would
 * be shorter and would defeat the point. Derived, this list is correct by
 * construction and a key added to a theme would flow straight through
 * unrecognised — serialised as an ordinary value, or silently dropped if it
 * needed a builder. Hardcoded, the test that compares it against the shipped
 * themes fails by name the moment the two disagree.
 *
 * `size` is on this list for a reason worth recording: it is easy to miss.
 * It sits between `space` and `surface` under a long comment, and a list of
 * theme keys written from memory tends to stop at `space`. Dropping it
 * produces no error — 34 of the app's 37 components destructure it, and
 * ThemeProvider feeds size.scrollbarW and size.focusRing into the global
 * stylesheet, where an undefined lands as `width: undefinedpx` and is
 * discarded. The scrollbar reverts to browser default and the focus ring
 * disappears, with nothing anywhere saying why. */
export const THEME_KEY_ORDER = [
  "id",
"label",
"colorScheme",
"blurb",
"palette",
"statusColor",
"levelColor",
"toastTone",
"actionCfg",
"type",
"radius",
"space",
"size",
"surface",
];

/* Which actionCfg entry each tint colour ended up in.
 *
 * This is what makes the tint recoverable without changing theme.jsx.
 * buildActionCfg copies tint.<name> to <action>.bg and tint.<name>B to
 * <action>.border verbatim — no alpha(), no mixing — so all 14 tint values
 * survive in the built theme and the mapping is a bijection: 7 actions by
 * {bg, border} is exactly the 14 keys the tint has. Nothing is inferred and
 * nothing is approximated.
 *
 * It is a bijection only while buildActionCfg keeps that shape. Give an
 * action a computed background and its tint value stops being readable back;
 * the round-trip test is what fails when that happens. */
const TINT_SOURCE = {
  green:  "copy_track",
  red:    "drop_track",
  amber:  "transcode_track",
  blue:   "change_container",
  yellow: "flag_manual_review",
  cyan:   "extract_subtitle",
  violet: "add_faststart",
};

/* The derived keys, and how each is rebuilt. One table drives both
 * inputsToTheme and themeToSource, so the object this module builds and the
 * source text it writes cannot disagree about which keys are derived. */
const DERIVED_BUILD = {
  statusColor: (i) => buildStatusColor(i.palette),
  levelColor:  (i) => buildLevelColor(i.palette),
  toastTone:   (i) => buildToastTone(i.palette),
  actionCfg:   (i) => buildActionCfg(i.palette, i.tint),
};

const DERIVED_CALL = {
  statusColor: (pal) => `buildStatusColor(${pal})`,
  levelColor:  (pal) => `buildLevelColor(${pal})`,
  toastTone:   (pal) => `buildToastTone(${pal})`,
  actionCfg:   (pal, inputs) =>
  `buildActionCfg(${pal}, ${emitObject(inputs.tint, 2, "tint")})`,
};

const isPlainObject = (v) =>
v !== null && typeof v === "object" && !Array.isArray(v);

const describe = (v) => {
  if (v === null) return "null";
  if (Array.isArray(v)) return "an array";
  return typeof v;
};

/* Every leaf in a theme is a string or a finite number, and this throws on
 * anything else rather than coercing it.
 *
 * Coercing is the tempting option and it is the wrong one here. A theme key
 * whose value is undefined fails silently — React drops a style property
 * with an undefined value, so the element renders without a background
 * instead of raising — and this module can produce partial themes in a loop.
 * A serialiser that quietly wrote `undefined` or `[object Object]` would
 * turn that one-off mistake into a repeatable one. */
const assertLeaf = (v, path) => {
  if (typeof v === "string") {
    /* An empty string is a string, and it is never a theme value. It emits
     * as a perfectly valid literal, so nothing downstream objects: the theme
     * loads, React sets padding to "" or background to "", the browser drops
     * the declaration, and the result is a component with no spacing or no
     * background and no error anywhere. That is the same silent failure as
     * an undefined key, arriving by a different route — and an editor where
     * clearing a field produces one makes it repeatable.
     *
     * No shipped theme has a blank leaf — checked across all six — so this
     * rejects nothing that exists today. */
    if (v.trim() === "") {
      throw new Error(
        `theme ${path}: empty, which renders as nothing rather than as an error`,
      );
    }
    return;
  }
  if (typeof v === "number" && Number.isFinite(v)) return;
  throw new Error(
    `theme ${path}: expected a string or a finite number, got ${describe(v)}`,
  );
};

const IDENTIFIER = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

const emitKey = (k) => (IDENTIFIER.test(k) ? k : JSON.stringify(k));

/* JSON.stringify, not manual quoting. It escapes backslashes and embedded
 * double quotes, and it leaves single quotes alone — which matters more here
 * than it looks, because type.root is a CSS font stack full of them
 * ("'JetBrains Mono Variable', 'JetBrains Mono', ..."). Emitting theme
 * strings in single quotes would terminate that value on its first
 * character and produce a block that does not parse. */
const emitScalar = (v, path) => {
  assertLeaf(v, path);
  return typeof v === "number" ? String(v) : JSON.stringify(v);
};

function emitObject(obj, indent, path) {
  const pad = " ".repeat(indent);
  const inner = " ".repeat(indent + 2);
  const lines = Object.entries(obj).map(([k, v]) => {
    const at = `${path}.${k}`;
    const rendered = isPlainObject(v)
    ? emitObject(v, indent + 2, at)
    : emitScalar(v, at);
    return `${inner}${emitKey(k)}: ${rendered},`;
  });
  return `{\n${lines.join("\n")}\n${pad}}`;
}

/* Reserved words that survive the id pattern below, plus theme.jsx's own
 * module-level bindings. An id becomes a `const` declaration in the emitted
 * block, so `const default = {...}` is a syntax error and `const themes =
 * {...}` shadows the export the app looks themes up in — both of which fail
 * at the point the block is pasted in rather than here, where the name was
 * chosen.
 *
 * The shipped ids are deliberately absent: re-emitting `terminal` is
 * what editing the terminal theme produces, and that block is a replacement
 * for the existing one rather than a name collision. Removing the old block
 * is the caller's job. */
const UNUSABLE_IDS = new Set([
  // Reserved words matching the id pattern.
  "arguments", "await", "break", "case", "catch", "class", "const",
  "continue", "debugger", "default", "delete", "do", "else", "enum", "eval",
  "export", "extends", "false", "finally", "for", "function", "if",
  "implements", "import", "in", "instanceof", "interface", "let", "new",
  "null", "package", "private", "protected", "public", "return", "static",
  "super", "switch", "this", "throw", "true", "try", "typeof", "var", "void",
  "while", "with", "yield",
  // theme.jsx's own module-level bindings that the id pattern below would
  // otherwise admit. Only these two: every other binding in that file
  // (ALPHA, LAYER, ThemeContext, buildStatusColor, DEFAULT_THEME_ID, the
  // React imports) contains a capital, and the pattern rejects those
  // already. Listing them here anyway would be listing names that cannot
  // collide.
  "themes", "alpha",
]);

/* Lowercase alphanumeric, starting with a letter. Every shipped id matches.
 *
 * Narrower than "valid JS identifier" on purpose. The id is three things at
 * once — a const name in the source, the key in `themes`, and the value
 * ThemeProvider persists to localStorage — so it has to survive all three,
 * and the intersection of what each accepts is smaller than any one of them.
 * Rejecting a hyphen here costs nothing; discovering it after a saved theme
 * fails to load costs an afternoon. */
const ID_PATTERN = /^[a-z][a-z0-9]*$/;

const assertUsableId = (id) => {
  if (typeof id !== "string" || !ID_PATTERN.test(id)) {
    throw new Error(
      `theme id ${JSON.stringify(id)}: must be lowercase letters and digits, ` +
      `starting with a letter`,
    );
  }
  if (UNUSABLE_IDS.has(id)) {
    throw new Error(
      `theme id ${JSON.stringify(id)}: reserved, or already a binding in theme.jsx`,
    );
  }
};

const clonePlain = (value, path) => {
  if (!isPlainObject(value)) {
    assertLeaf(value, path);
    return value;
  }
  const out = {};
  for (const [k, v] of Object.entries(value)) {
    out[k] = clonePlain(v, `${path}.${k}`);
  }
  return out;
};

/* Every leaf path in a value, as dotted strings. A leaf is anything that is
 * not a plain object, so this records depth as well as naming: a group where
 * a colour belongs shows up as a different path rather than the same one. */
const leafPaths = (value, path = "") => {
  if (!isPlainObject(value)) return [path];
  return Object.entries(value).flatMap(([k, v]) =>
  leafPaths(v, path ? `${path}.${k}` : k),
  );
};

/* The shape every theme must have, taken from the default theme.
 *
 * This enforces a contract theme.jsx's own header already states — "Keep
 * every key present, a missing key is a runtime undefined, not a fallback.
 * Keep the SHAPE identical; only values should differ" — which until now
 * nothing checked. Leaf-type validation alone does not cover it: a palette
 * entry written as { hex: "#fff" } instead of "#fff" emits a valid literal
 * and contains a valid string, so every type check passes and the theme is
 * still broken.
 *
 * Derived from the shipped theme at runtime, unlike THEME_KEY_ORDER above,
 * and the difference is the point. THEME_KEY_ORDER drives GENERATION, so
 * deriving it would let a key nobody taught this module about pass through
 * unrecognised. This drives VALIDATION, where deriving it is what makes a
 * key added to theme.jsx immediately required of every theme saved after. */
const REFERENCE_SHAPE = new Set(leafPaths(themes[DEFAULT_THEME_ID]));

const assertThemeShape = (theme) => {
  const actual = new Set(leafPaths(theme));
  const missing = [...REFERENCE_SHAPE].filter((p) => !actual.has(p));
  const extra = [...actual].filter((p) => !REFERENCE_SHAPE.has(p));
  if (missing.length === 0 && extra.length === 0) return;
  const parts = [];
  if (missing.length) parts.push(`missing ${missing.join(", ")}`);
  if (extra.length) parts.push(`unexpected ${extra.join(", ")}`);
  throw new Error(
    `theme shape does not match ${DEFAULT_THEME_ID}: ${parts.join("; ")}`,
  );
};

/**
 * Recover the per-theme tint from a built theme object.
 *
 * Throws rather than returning a partial tint: a tint missing a key
 * regenerates an actionCfg with an undefined background, which is the
 * silent-failure case this module exists to make impossible.
 */
export const recoverTint = (theme) => {
  const out = {};
  for (const [name, action] of Object.entries(TINT_SOURCE)) {
    const cfg = theme?.actionCfg?.[action];
    if (!isPlainObject(cfg)) {
      throw new Error(
        `recoverTint: actionCfg.${action} is missing, so the tint cannot be ` +
        `recovered from this theme`,
      );
    }
    assertLeaf(cfg.bg, `actionCfg.${action}.bg`);
    assertLeaf(cfg.border, `actionCfg.${action}.border`);
    out[name] = cfg.bg;
    out[`${name}B`] = cfg.border;
  }
  return out;
};

/**
 * Split a built theme into the values a person chose.
 *
 * Deep-copies every group. `themes` is a module singleton the running app
 * reads on every render, so an editor handed live references would repaint
 * the app from the draft on the first keystroke — and worse, would leave the
 * shipped theme permanently altered after the editor closed.
 */
export const themeToInputs = (theme) => {
  const out = {};
  for (const key of THEME_KEY_ORDER) {
    if (key in DERIVED_BUILD) continue;
    out[key] = clonePlain(theme[key], key);
    // Grouped next to the palette it tints rather than appended at the end,
    // since the two are edited together.
    if (key === "palette") out.tint = recoverTint(theme);
  }
  return out;
};

/**
 * Build a live theme object from a set of inputs.
 *
 * Key order comes from THEME_KEY_ORDER by construction, not by a second
 * hand-written literal that could drift from it.
 */
export const inputsToTheme = (inputs) => {
  const out = {};
  for (const key of THEME_KEY_ORDER) {
    out[key] = key in DERIVED_BUILD ? DERIVED_BUILD[key](inputs) : inputs[key];
  }
  return out;
};

/**
 * Emit the source text of a theme block for theme.jsx.
 *
 * Produces the same shape as the blocks already in the file — a palette
 * const, then the theme const with the derived maps as calls to the builder
 * functions — so a saved theme is indistinguishable from a hand-written one
 * and stays editable by hand afterwards.
 *
 * Alignment is not reproduced. The existing blocks are aligned by hand, and
 * a generator guessing at column positions produces diff noise on every save
 * for no behavioural gain; the values are what has to be exact.
 */
export const themeToSource = (inputs) => {
  assertUsableId(inputs.id);
  /* Validate the theme this block will evaluate to, not the inputs.
   *
   * It is the built object that has to satisfy the shape contract, and
   * building it exercises the derived maps as well — a palette entry that is
   * not a colour surfaces in statusColor and toastTone too, so one check
   * covers what four would.
   *
   * Deliberately here and not inside inputsToTheme. That function feeds the
   * live preview, which runs on every keystroke, and a field cleared halfway
   * through being retyped should repaint as something visibly wrong rather
   * than throw. Saving is the boundary where a theme has to be whole. */
  assertThemeShape(inputsToTheme(inputs));

  const palette = `${inputs.id}Palette`;

  const body = THEME_KEY_ORDER.map((key) => {
    if (key === "palette") return `  palette: ${palette},`;
    if (key in DERIVED_CALL) return `  ${key}: ${DERIVED_CALL[key](palette, inputs)},`;
    const v = inputs[key];
    const rendered = isPlainObject(v)
    ? emitObject(v, 2, key)
    : emitScalar(v, key);
    return `  ${key}: ${rendered},`;
  });

  return [
    `const ${palette} = ${emitObject(inputs.palette, 0, "palette")};`,
    ``,
    `const ${inputs.id} = {`,
      ...body,
      `};`,
      ``,
  ].join("\n");
};
