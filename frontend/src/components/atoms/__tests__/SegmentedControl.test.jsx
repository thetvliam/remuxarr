/**
 * SegmentedControl — one selected option in a strip of buttons.
 *
 * Built to replace History's tabs and the log viewer's level filter, which
 * each set the border shorthand and then borderRight: "none" on the same
 * button. React applies both on a first render and only the shorthand when
 * just its value changes, so the selected segment lost its right edge on
 * load and got it back, with every border doubled, after a click. The tests
 * in HistoryPanel.test.jsx and LogViewer.test.jsx pin that through the two
 * real controls; these pin the component's own contract.
 *
 * The three options differ in colour, so a segment drawn in another
 * segment's colour is visible.
 *
 * Verified by mutation. Before these tests and the History and LogViewer
 * ones existed, the new code was run against the unchanged 608-test suite
 * with ten mutants. Three were already killed by existing tests and are not
 * claimed: the click reporting the first option's value, and History's and
 * the log viewer's onChange left unwired. Seven survived: the shorthand and
 * borderRight: "none" put back, which is the original bug; the selected
 * option not raised; the 1px overlap removed; every option drawn in the
 * first option's colour; aria-pressed inverted; History showing "success"
 * whatever its tab; and the log viewer showing INFO whatever its filter.
 * All ten are killed now, each by the test written for it.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SegmentedControl } from "../SegmentedControl";
import { ThemeProvider } from "../../../theme";

const OPTIONS = [
  { value: "a", label: "ALPHA", color: "rgb(170, 0, 0)" },
  { value: "b", label: "BRAVO", color: "rgb(0, 170, 0)", badge: "12", title: "12 items" },
  { value: "c", label: "CHARLIE", color: "rgb(0, 0, 170)" },
];

function renderControl(value, onChange = vi.fn()) {
  const view = render(
    <ThemeProvider>
      <SegmentedControl label="Things" options={OPTIONS} value={value} onChange={onChange} />
    </ThemeProvider>,
  );
  const rerender = (next) => view.rerender(
    <ThemeProvider>
      <SegmentedControl label="Things" options={OPTIONS} value={next} onChange={onChange} />
    </ThemeProvider>,
  );
  return { onChange, rerender };
}

const button = (label) => screen.getByRole("button", { name: new RegExp(`^${label}`) });
const sides = (el) => ["Top", "Right", "Bottom", "Left"].map(s => el.style[`border${s}Color`]);

describe("SegmentedControl", () => {
  it("outlines the selected option in its own colour on every side", () => {
    const { rerender } = renderControl("b");
    expect(sides(button("BRAVO"))).toEqual(Array(4).fill("rgb(0, 170, 0)"));

    rerender("c");
    expect(sides(button("CHARLIE"))).toEqual(Array(4).fill("rgb(0, 0, 170)"));
  });

  it("keeps every side the same when the selection moves away and back", () => {
    const { rerender } = renderControl("a");
    const before = ["Top", "Right", "Bottom", "Left"].map(s => button("ALPHA").style[`border${s}`]);

    rerender("b");
    rerender("a");

    expect(["Top", "Right", "Bottom", "Left"].map(s => button("ALPHA").style[`border${s}`]))
      .toEqual(before);
  });

  it("raises the selected option over the edges it shares", () => {
    renderControl("b");

    expect([button("ALPHA"), button("BRAVO"), button("CHARLIE")].map(b => b.style.zIndex))
      .toEqual(["0", "1", "0"]);
  });

  it("overlaps each option after the first by one pixel, so shared edges are single", () => {
    renderControl("a");

    expect([button("ALPHA"), button("BRAVO"), button("CHARLIE")].map(b => b.style.marginLeft))
      .toEqual(["0px", "-1px", "-1px"]);
  });

  it("marks only the selected option as pressed", () => {
    renderControl("c");

    expect([button("ALPHA"), button("BRAVO"), button("CHARLIE")]
      .map(b => b.getAttribute("aria-pressed"))).toEqual(["false", "false", "true"]);
  });

  it("reports the option that was clicked", () => {
    const { onChange } = renderControl("a");

    fireEvent.click(button("CHARLIE"));
    fireEvent.click(button("BRAVO"));

    expect(onChange.mock.calls).toEqual([["c"], ["b"]]);
  });

  it("shows an option's badge and tooltip", () => {
    renderControl("a");

    expect(button("BRAVO").textContent).toBe("BRAVO12");
    expect(button("BRAVO").title).toBe("12 items");
  });
});

describe("SegmentedControl — fill", () => {
  /* For a strip given a row of its own, History's tabs. Without it a
   * squeezed segment broke its label across lines ("DRY / RUN"), and at its
   * narrowest the strip ran out of the panel. Measured in Chrome after, in
   * every theme from 320px with five-digit counts, no label breaks and
   * nothing runs out: the segments share the row equally, each label holds
   * together, and a badge drops beneath its label when they cannot sit side
   * by side. jsdom has no layout, so these pin the rules that does it.
   *
   * Without fill the strip is unchanged, which the log viewer relies on:
   * pinned too, so the option cannot quietly become the default.
   *
   * Verified by mutation: the strip not stretched, the segments not shared
   * out, the label free to break, and the badge free to break, all survived
   * the suite without this block and are killed by it. */
  const renderStrip = (fill) => render(
    <ThemeProvider>
      <SegmentedControl label="Things" options={OPTIONS} value="a" onChange={() => {}} fill={fill} />
    </ThemeProvider>,
  );
  const strip = () => screen.getByRole("group", { name: "Things" });
  const segments = () => ["ALPHA", "BRAVO", "CHARLIE"].map(button);

  it("stretches the strip across its row in equal segments", () => {
    renderStrip(true);

    expect(strip().style.width).toBe("100%");
    expect(segments().map(b => [b.style.flexGrow, b.style.flexShrink, b.style.flexBasis]))
      .toEqual(Array(3).fill(["1", "1", "0px"]));
  });

  it("keeps each label on one line, and lets the badge drop beneath it whole", () => {
    renderStrip(true);

    const [label, badge] = button("BRAVO").querySelectorAll("span");
    expect(label.textContent).toBe("BRAVO");
    expect(label.style.whiteSpace).toBe("nowrap");
    expect(badge.textContent).toBe("12");
    expect([badge.style.display, badge.style.whiteSpace]).toEqual(["inline-block", "nowrap"]);
  });

  it("leaves the strip as it was without it", () => {
    renderStrip(false);

    expect(strip().style.width).toBe("");
    expect(segments().map(b => b.style.flexBasis)).toEqual(["", "", ""]);
    expect(button("ALPHA").querySelector("span")).toBeNull();
    expect(button("BRAVO").querySelector("span").style.whiteSpace).toBe("");
  });
});
