/**
 * PanelHeader — the optional second row.
 *
 * History's tabs used to share the title row with HISTORY, the count and
 * RETRY ALL or CLEAR ALL. That row needs 517px to 679px depending on the
 * theme and the counts, measured in Chrome, and on a phone or a half-width
 * desktop panel the tabs broke their labels and ran out of the panel.
 * `below` gives them a row of their own, across the panel's full width.
 *
 * The Queue and Forge panels pass no second row, and their headers have to
 * stay exactly as they were; measured, they are pixel-identical. These pin
 * the rules on both sides, since jsdom has no layout.
 *
 * Verified by mutation: the second row not given the full width, the header
 * not allowed to wrap with one, and the header wrapping without one, all
 * survived the suite without this file and are killed by it.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PanelHeader } from "../PanelHeader";
import { ThemeProvider } from "../../../theme";

const header = () => screen.getByText("THINGS").parentElement;

describe("PanelHeader", () => {
  it("puts a second row under the title, across the full width", () => {
    render(
      <ThemeProvider>
        <PanelHeader label="THINGS" count={3} right={<button>ACT</button>} below={<span>ROW TWO</span>} />
      </ThemeProvider>,
    );

    expect(header().style.flexWrap).toBe("wrap");
    const row = screen.getByText("ROW TWO").parentElement;
    expect(row.parentElement).toBe(header());
    expect(row.style.flexBasis).toBe("100%");
    // The title row's own control stays on the title row, ahead of it.
    expect(header().lastElementChild).toBe(row);
    expect(screen.getByRole("button", { name: "ACT" }).parentElement.nextElementSibling).toBe(row);
  });

  it("is a single row, as before, without one", () => {
    render(
      <ThemeProvider>
        <PanelHeader label="THINGS" count={3} right={<button>ACT</button>} />
      </ThemeProvider>,
    );

    expect(header().style.flexWrap).toBe("");
    expect(header().children).toHaveLength(3);
  });
});
