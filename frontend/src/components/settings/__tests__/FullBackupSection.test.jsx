/**
 * FullBackupSection — its two rows on a narrow phone.
 *
 * The confirmation box and REPLACE DATABASE share a row. The button never
 * shrinks or wraps its label, so at 320px wide it ran 39px off the screen,
 * measured in Chrome. The row now wraps, so the button drops below the box
 * instead; measured after, it is on screen at 320, 360 and 390px, and at
 * 1280px and 900px the page is pixel-identical to before. jsdom has no
 * layout, so this pins the rule that measurement showed to be needed.
 *
 * Only the wrap. The box needs no basis: a text input's default width
 * already keeps it from shrinking, and with flex: 1 the row wrapped at the
 * same widths.
 *
 * Verified by mutation: removing the wrap passed every test before this file
 * existed and is killed by it.
 *
 * The export row is an ActionRow, with its button below the text on a phone
 * and beside it on a desktop, as the other action rows on the page.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FullBackupSection } from "../FullBackupSection";
import { ThemeProvider } from "../../../theme";
import { stubViewport } from "../../../test/viewport";

describe("FullBackupSection — on a narrow phone", () => {
  it("lets REPLACE DATABASE drop below the confirmation box", () => {
    render(
      <ThemeProvider>
        <FullBackupSection api="" toast={() => {}} />
      </ThemeProvider>,
    );

    const box = screen.getByPlaceholderText('Type "REPLACE DATABASE" to confirm');
    const row = screen.getByRole("button", { name: "REPLACE DATABASE" }).parentElement;
    expect(box.parentElement).toBe(row);
    expect(row.style.flexWrap).toBe("wrap");
  });
});

describe("FullBackupSection — the export row", () => {
  it.each([[390, "column"], [1280, "row"]])("at %ipx lays its row out as a %s", (width, direction) => {
    stubViewport(width);
    render(
      <ThemeProvider>
        <FullBackupSection api="" toast={() => {}} />
      </ThemeProvider>,
    );

    expect(screen.getByRole("button", { name: "EXPORT" }).parentElement.style.flexDirection).toBe(direction);
  });
});
