/**
 * TabBar — which of two panels a phone shows.
 *
 * Replaces two identical hand-written tab rows (the dashboard's Queue and
 * History, Forge's Candidates and Processed). Their wiring is pinned in
 * App.test.jsx and ForgePage.test.jsx; these pin the component itself.
 *
 * Verified by mutation, run against the 621-test suite without this file,
 * App.test.jsx or ForgePage.test.jsx, where all three survived, and again
 * with them, where all three are killed by the test written for each: the
 * underline drawn on the unselected tabs instead of the selected one; the
 * click reporting the first tab's value; and aria-pressed inverted. The last
 * two are also killed by the App and Forge tests.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TabBar } from "../TabBar";
import { ThemeProvider } from "../../../theme";

const TABS = [
  { value: "left", label: "LEFT" },
  { value: "middle", label: "MIDDLE" },
  { value: "right", label: "RIGHT" },
];

function renderBar(value, onChange = vi.fn()) {
  render(
    <ThemeProvider>
      <TabBar label="Panels" tabs={TABS} value={value} onChange={onChange} />
    </ThemeProvider>,
  );
  return onChange;
}

const tab = (name) => screen.getByRole("button", { name, exact: true });

describe("TabBar", () => {
  it("underlines only the selected tab, in its text colour", () => {
    renderBar("middle");

    const [left, middle, right] = [tab("LEFT"), tab("MIDDLE"), tab("RIGHT")];
    expect(middle.style.borderBottomColor).toBe(middle.style.color);
    expect(middle.style.borderBottomColor).not.toBe("transparent");
    expect([left.style.borderBottomColor, right.style.borderBottomColor])
      .toEqual(["transparent", "transparent"]);
  });

  it("marks only the selected tab as pressed", () => {
    renderBar("right");

    expect([tab("LEFT"), tab("MIDDLE"), tab("RIGHT")].map(b => b.getAttribute("aria-pressed")))
      .toEqual(["false", "false", "true"]);
  });

  it("reports the tab that was clicked", () => {
    const onChange = renderBar("left");

    fireEvent.click(tab("RIGHT"));
    fireEvent.click(tab("MIDDLE"));

    expect(onChange.mock.calls).toEqual([["right"], ["middle"]]);
  });
});
