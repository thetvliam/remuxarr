/* ═══════════════════════════════════════════════════════════════════════════
 * STYLE CONFLICTS
 * Finds object literals that set a CSS shorthand and one of its own
 * longhands, such as `border` with `borderRight`. Used by
 * src/__tests__/styleConflicts.test.js over the whole frontend source.
 *
 * Why it matters: React sets a style property only when its value changes.
 * Given `border` and `borderRight` on one element, the first render applies
 * both in order; a later render in which only `border` changed applies
 * `border` alone, which resets every side, and the unchanged `borderRight`
 * is not put back. History's tabs lost and regained their right edge that
 * way. Whether a given pairing can misbehave depends on which value changes
 * when, which is easy to get wrong while editing, so the rule is simply not
 * to pair them.
 *
 * Every style in this app is an inline object, so any object literal is
 * checked; one that is not a style is very unlikely to use these names
 * together. Keys that are computed or spread in cannot be read statically
 * and are left alone.
 *
 * Parsed with @babel/parser, a dev dependency for this reason.
 ═══════════════════════════════════════════════════════════════════════════ */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "@babel/parser";

/** Each shorthand, and the longhands that setting it resets. A list of
 *  pairs rather than an object literal: an object keyed by these names is
 *  exactly what findConflicts looks for, and would report itself. */
export const SHORTHANDS = new Map([
  ["border", /^border(Top|Right|Bottom|Left|Width|Style|Color)/],
  ["borderTop", /^borderTop(Width|Style|Color)$/],
  ["borderRight", /^borderRight(Width|Style|Color)$/],
  ["borderBottom", /^borderBottom(Width|Style|Color)$/],
  ["borderLeft", /^borderLeft(Width|Style|Color)$/],
  ["borderColor", /^border(Top|Right|Bottom|Left)Color$/],
  ["borderWidth", /^border(Top|Right|Bottom|Left)Width$/],
  ["borderStyle", /^border(Top|Right|Bottom|Left)Style$/],
  ["borderRadius", /^border(Top|Bottom)(Left|Right)Radius$/],
  ["padding", /^padding(Top|Right|Bottom|Left|Inline|Block)/],
  ["margin", /^margin(Top|Right|Bottom|Left|Inline|Block)/],
  ["inset", /^(top|right|bottom|left)$/],
  ["background", /^background[A-Z]/],
  ["font", /^font[A-Z]/],
  ["flex", /^flex(Grow|Shrink|Basis)$/],
  ["overflow", /^overflow[XY]$/],
  ["outline", /^outline[A-Z]/],
  ["gap", /^(rowGap|columnGap)$/],
  ["transition", /^transition[A-Z]/],
  ["gridTemplate", /^gridTemplate[A-Z]/],
]);

const keyName = (prop) =>
  prop.type === "ObjectProperty" && !prop.computed
    ? (prop.key.name ?? prop.key.value)
    : null;

/** Conflicts in one file's source: [{ line, shorthand, longhands }]. */
export function findConflicts(source) {
  const ast = parse(source, { sourceType: "module", plugins: ["jsx"] });
  const found = [];
  const visit = (node) => {
    if (!node || typeof node.type !== "string") return;
    if (node.type === "ObjectExpression") {
      const keys = node.properties.map(keyName).filter(Boolean);
      for (const [shorthand, longhand] of SHORTHANDS) {
        if (!keys.includes(shorthand)) continue;
        const longhands = keys.filter(k => k !== shorthand && longhand.test(k));
        if (longhands.length) {
          found.push({ line: node.loc.start.line, shorthand, longhands });
        }
      }
    }
    for (const [key, value] of Object.entries(node)) {
      if (key === "loc" || key === "start" || key === "end") continue;
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === "object") visit(value);
    }
  };
  visit(ast.program);
  return found;
}

/** Every .js and .jsx file under dir, at any depth, as paths from dir. */
export function sourceFiles(dir, prefix = "") {
  return readdirSync(join(dir, prefix), { withFileTypes: true }).flatMap(entry => {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) return sourceFiles(dir, path);
    return /\.jsx?$/.test(entry.name) ? [path] : [];
  });
}

/** Conflicts across every source file under dir, as "file:line" strings. */
export function findConflictsIn(dir) {
  return sourceFiles(dir).flatMap(file =>
    findConflicts(readFileSync(join(dir, file), "utf8")).map(c =>
      `${file}:${c.line}  ${c.shorthand} with ${c.longhands.join(", ")}`));
}
