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
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

describe("FullBackupSection — the secrets option", () => {
  /* As in BackupRestoreSection: the shared OnOffSwitch, where it was a
   * checkbox. On by default, and switched off the backup leaves the secrets
   * out. Verified by mutation: the switch no longer changing the option
   * survived the suite before this and is killed by it. */
  it("exports with secrets by default, and without them once switched off", () => {
    const hrefs = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function () { hrefs.push(this.getAttribute("href")); });
    render(
      <ThemeProvider>
        <FullBackupSection api="" toast={() => {}} />
      </ThemeProvider>,
    );
    const exportBtn = screen.getByRole("button", { name: "EXPORT" });

    fireEvent.click(exportBtn);
    fireEvent.click(screen.getByRole("switch", { name: "Include connection secrets" }));
    fireEvent.click(exportBtn);

    expect(hrefs).toEqual([
      "/api/backup/export?include_secrets=true",
      "/api/backup/export?include_secrets=false",
    ]);
  });
});
