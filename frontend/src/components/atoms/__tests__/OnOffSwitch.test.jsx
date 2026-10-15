/**
 * OnOffSwitch — the Settings page's one on/off control.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The ■ ON / □ OFF button was written inside SettingInput, and two other
 * places on the page drew their own on/off control: Maintenance's sliding
 * switches and Backup's two "Include connection secrets" checkboxes. All of
 * them use this now, so what it reports and what a click asks for are
 * pinned here once, for every switch on the page.
 *
 * Verified by mutation: a click asking for the same value again, and the
 * ON and OFF text swapped, both survived the whole suite before this file
 * and are killed by it. Reporting the opposite state was already caught,
 * by Maintenance's settings-load tests and the live-toggle test, so it is
 * not claimed here.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OnOffSwitch } from "../OnOffSwitch";
import { ThemeProvider } from "../../../theme";

const setup = (on) => {
  const onChange = vi.fn();
  render(
    <ThemeProvider>
      <OnOffSwitch on={on} onChange={onChange} label="Thing" />
    </ThemeProvider>,
  );
  return { onChange, sw: screen.getByRole("switch", { name: "Thing" }) };
};

describe("OnOffSwitch", () => {
  it.each([[true, "true", "■ ON"], [false, "false", "□ OFF"]])(
    "when on is %s, reports aria-checked %s and reads %s", (on, checked, text) => {
      const { sw } = setup(on);

      expect(sw).toHaveAttribute("aria-checked", checked);
      expect(sw).toHaveTextContent(text);
    });

  it.each([true, false])("asks for the other value when clicked while %s", async (on) => {
    const { sw, onChange } = setup(on);

    await userEvent.setup().click(sw);

    expect(onChange).toHaveBeenCalledWith(!on);
  });
});
