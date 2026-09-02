/**
 * TagInput — the chip editor behind every string_list setting.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * add() was `if (v && !values.includes(v))`, so re-adding an existing entry
 * did nothing at all and did not even clear the box: the text sat there
 * looking unsubmitted, which is what a keypress that had not registered looks
 * like. With normalize on, "ENG" and "eng" are the same entry, so the chips
 * above give no way to work out why nothing happened.
 *
 * TimeTagInput in MaintenanceSection is the same control for schedule times
 * and already reported this ("That time is already in the list"), along with
 * accepting a comma as a separator and Escape to clear. TagInput's own
 * comment calls the two "the same control, same glyph, so it should read the
 * same way", about their remove buttons — this file covers the rest of it.
 *
 * normalize is the other thing worth pinning: false is passed for scan_paths
 * and plex_path_mappings, where lowercasing breaks a mapping on a
 * case-sensitive filesystem.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TagInput } from "../TagInput";
import { ThemeProvider } from "../../../theme";

const setup = (props = {}) => {
  const onChange = vi.fn();
  render(
    <ThemeProvider>
      <TagInput values={["eng"]} onChange={onChange} label="Keep audio" {...props} />
    </ThemeProvider>,
  );
  return { onChange, box: screen.getByLabelText("Add to Keep audio") };
};

describe("TagInput", () => {
  it("adds a new entry on Enter", async () => {
    const user = userEvent.setup();
    const { onChange, box } = setup();

    await user.type(box, "fre{Enter}");

    expect(onChange).toHaveBeenCalledWith(["eng", "fre"]);
    expect(box).toHaveValue("");
  });

  it("adds on comma too, as its sibling does", async () => {
    const user = userEvent.setup();
    const { onChange } = setup();

    await user.type(screen.getByLabelText("Add to Keep audio"), "fre,");

    expect(onChange).toHaveBeenCalledWith(["eng", "fre"]);
  });

  it("says so when the entry is already there", async () => {
    const user = userEvent.setup();
    const { onChange, box } = setup();

    await user.type(box, "eng{Enter}");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/already in the list/i);
  });

  it("catches a duplicate that only differs in case", async () => {
    // The case that is impossible to diagnose from the chips: normalize
    // lowercases before comparing, so ENG and eng are one entry.
    const user = userEvent.setup();
    const { onChange } = setup();

    await user.type(screen.getByLabelText("Add to Keep audio"), "ENG{Enter}");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("keeps the rejected text, so it can be corrected rather than retyped", async () => {
    const user = userEvent.setup();
    const { box } = setup();

    await user.type(box, "eng{Enter}");

    expect(box).toHaveValue("eng");
  });

  it("clears the complaint once the text changes", async () => {
    const user = userEvent.setup();
    const { box } = setup();
    await user.type(box, "eng{Enter}");
    expect(screen.getByRole("alert")).toBeInTheDocument();

    await user.type(box, "x");

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("Escape clears the box and the complaint", async () => {
    const user = userEvent.setup();
    const { box } = setup();
    await user.type(box, "eng{Enter}");

    await user.type(box, "{Escape}");

    expect(box).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores an empty entry without complaining", async () => {
    // Enter on an empty box is not a mistake worth a red line.
    const user = userEvent.setup();
    const { onChange, box } = setup();

    await user.type(box, "   {Enter}");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("preserves case when normalize is off, as paths require", async () => {
    const user = userEvent.setup();
    const { onChange } = setup({ values: [], normalize: false });

    await user.type(screen.getByLabelText("Add to Keep audio"), "/media/TV{Enter}");

    expect(onChange).toHaveBeenCalledWith(["/media/TV"]);
  });

  it("compares case-sensitively when normalize is off", async () => {
    // /media/TV and /media/tv are different directories on ext4.
    const user = userEvent.setup();
    const { onChange } = setup({ values: ["/media/tv"], normalize: false });

    await user.type(screen.getByLabelText("Add to Keep audio"), "/media/TV{Enter}");

    expect(onChange).toHaveBeenCalledWith(["/media/tv", "/media/TV"]);
  });

  it("adds from the + button as well as the keyboard", async () => {
    const user = userEvent.setup();
    const { onChange, box } = setup();

    await user.type(box, "fre");
    await user.click(screen.getByLabelText("Add"));

    expect(onChange).toHaveBeenCalledWith(["eng", "fre"]);
  });
});
