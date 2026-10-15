/**
 * ActionRow — a settings action section's title, description and action.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The action sections wrote this row out by hand, nine times, with three
 * rules for a narrow screen between them. ActionRow is the one row now, and
 * the one rule: on a phone the action goes below the text, which keeps the
 * full width; on a desktop it sits beside. The width is read through
 * useBreakpoint, as every layout decision is, so these stub the viewport.
 * jsdom has no layout, so they pin the direction the row asks for. Each
 * section's own test file checks that its rows do the same.
 *
 * Verified by mutation: the row never stacking and the row stacking on a
 * desktop too both survived the suite without these tests and the sections'
 * row checks (less the four narrow-phone tests those replace, which pinned
 * the old wrap and fail on this code by themselves). Each is killed here
 * and by every one of the sections' checks.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ActionRow } from "../ActionRow";
import { ThemeProvider } from "../../../theme";
import { stubViewport } from "../../../test/viewport";

/** The row at `width`, found from its action. */
const rowAt = (width) => {
  stubViewport(width);
  render(
    <ThemeProvider>
      <ActionRow title="Do it" description="What doing it does." action={<button>GO</button>} />
    </ThemeProvider>,
  );
  return screen.getByRole("button", { name: "GO" }).parentElement;
};

describe("ActionRow", () => {
  it("puts the action below the text on a phone, the text keeping the full width", () => {
    const row = rowAt(390);

    expect(row.style.flexDirection).toBe("column");
    expect(row.firstElementChild.style.alignSelf).toBe("stretch");
    expect(row.firstElementChild).toHaveTextContent("Do itWhat doing it does.");
  });

  it("puts the action beside the text on a desktop", () => {
    const row = rowAt(1280);

    expect(row.style.flexDirection).toBe("row");
  });
});
