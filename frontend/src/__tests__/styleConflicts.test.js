/**
 * No style object sets a shorthand alongside one of its own longhands.
 *
 * History's tabs set `border` and `borderRight: "none"` on one button. React
 * applied both on the first render and only `border` when just its value
 * changed, so the selected tab lost its right edge on load and got it back,
 * with every border doubled, after a click. Fourteen other objects paired a
 * shorthand with a longhand, harmless only because of which value happened
 * to change when. Three went with the shared tab components; the other
 * eleven were converted to longhands, and this keeps it that way.
 *
 * The detector itself (src/test/styleConflicts.js) is pinned first, on small
 * pieces of source, so that the last test passing means "nothing found"
 * rather than "nothing looked for".
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

import { findConflicts, findConflictsIn, sourceFiles } from "../test/styleConflicts";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

const conflicts = (code) => findConflicts(code).map(c => [c.shorthand, c.longhands]);

describe("the style-conflict detector", () => {
  it("finds a border shorthand set with one of its sides", () => {
    expect(conflicts(`const s = { border: "1px solid red", borderRight: "none" };`))
      .toEqual([["border", ["borderRight"]]]);
  });

  it("finds the other shorthand families", () => {
    expect(conflicts(`
      a({ padding: 4, paddingLeft: 8 });
      b({ inset: 0, top: 10 });
      c({ background: "red", backgroundColor: "blue" });
    `)).toEqual([
      ["padding", ["paddingLeft"]],
      ["inset", ["top"]],
      ["background", ["backgroundColor"]],
    ]);
  });

  it("finds one inside JSX, and says on which line", () => {
    const found = findConflicts(`const X = () => (
      <div style={{
        border: "none",
        borderBottom: "1px solid red",
      }} />);`);
    expect(found).toEqual([{ line: 2, shorthand: "border", longhands: ["borderBottom"] }]);
  });

  it("does not flag sides set on their own, or a shorthand on its own", () => {
    expect(conflicts(`
      a({ borderTop: "none", borderRight: "none", borderBottom: "1px solid red", borderLeft: "none" });
      b({ border: "1px solid red", borderRadius: 2 });
      c({ padding: 4, margin: 2 });
    `)).toEqual([]);
  });

  it("leaves computed and spread keys alone, since they cannot be read", () => {
    expect(conflicts(`
      a({ border: "none", [side]: "1px solid red" });
      b({ ...base, borderRight: "none" });
    `)).toEqual([]);
  });

  it("reads every .js and .jsx file, however deep", () => {
    const files = sourceFiles(SRC);
    expect(files).toContain("App.jsx");
    expect(files).toContain("components/atoms/SegmentedControl.jsx");
    expect(files).toContain("hooks/useAppData.js");
  });
});

describe("the frontend source", () => {
  it("has no style object setting a shorthand and one of its longhands", () => {
    expect(findConflictsIn(SRC)).toEqual([]);
  });
});
