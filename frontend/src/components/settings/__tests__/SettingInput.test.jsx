/**
 * SettingInput — IntegerInput blur behaviour.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `draft` is null until the first keystroke. onBlur parsed it unconditionally,
 * and parseInt(null ?? "") is NaN, which fell through to the "user cleared the
 * field" fallback and overwrote the stored value. Focusing and leaving a field
 * — or simply tabbing through the Worker settings — silently changed all five
 * integer settings:
 *
 *   und_audio_threshold      2   -> 1    every single-und file to manual review
 *   max_concurrent_jobs      1   -> 0
 *   job_timeout_minutes    120   -> 0    the worker reads this as
 *                                        `float(m) * 60 if m else None`, so 0
 *                                        removes the timeout and a hung FFmpeg
 *                                        job runs forever
 *   email_smtp_port        587   -> 0    all outgoing mail broken
 *   email_failure_threshold  5   -> 0    breaker trips on the first failure
 *
 * The field was left dirty, so the SaveBar showed "1 unsaved change" — but
 * nobody associates that with a field they only tabbed past, and it rides
 * along with the next deliberate edit.
 *
 * The tests below are deliberately split: the first group pins that an
 * untouched field commits NOTHING, and the second pins that the fallback
 * still works for the case it was actually written for. A fix that simply
 * never called onChange would pass the first group and break the second.
 */
import { useState } from "react";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SettingInput } from "../SettingInput";
import { ThemeProvider } from "../../../theme";

const field = (over = {}) => ({
  key: "und_audio_threshold",
  label: "Undefined audio threshold",
  type: "integer",
  min: 1,
  ...over,
});

function renderInput(f, value) {
  const onChange = vi.fn();
  render(
    <ThemeProvider>
      <SettingInput field={f} value={value} onChange={onChange} />
    </ThemeProvider>,
  );
  return { onChange, input: screen.getByLabelText(f.label) };
}

// The five integer settings in the real schema, with their production
// defaults and the value the old blur handler resolved them to.
const INTEGER_SETTINGS = [
  { key: "und_audio_threshold", min: 1, stored: 2, oldResult: 1 },
  { key: "max_concurrent_jobs", min: undefined, stored: 1, oldResult: 0 },
  { key: "job_timeout_minutes", min: undefined, stored: 120, oldResult: 0 },
  { key: "email_smtp_port", min: undefined, stored: 587, oldResult: 0 },
  { key: "email_failure_threshold", min: undefined, stored: 5, oldResult: 0 },
];

describe("IntegerInput — untouched field", () => {
  it.each(INTEGER_SETTINGS)(
    "$key is not changed by focus + blur with no typing",
    async ({ key, min, stored, oldResult }) => {
      const user = userEvent.setup();
      const f = field({ key, label: key, min });
      const { onChange, input } = renderInput(f, stored);

      await user.click(input);
      await user.tab();

      expect(onChange, `blur wrote ${oldResult} over the stored ${stored}`)
        .not.toHaveBeenCalled();
      expect(input).toHaveValue(stored);
    },
  );

  it("tabbing straight through several fields changes nothing", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ThemeProvider>
        <SettingInput field={field({ key: "a", label: "a" })} value={2} onChange={onChange} />
        <SettingInput field={field({ key: "b", label: "b", min: undefined })} value={120} onChange={onChange} />
        <SettingInput field={field({ key: "c", label: "c", min: undefined })} value={587} onChange={onChange} />
      </ThemeProvider>,
    );

    await user.click(screen.getByLabelText("a"));
    await user.tab();
    await user.tab();
    await user.tab();

    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("IntegerInput — the fallback still works", () => {
  it("commits a typed value on blur", async () => {
    const user = userEvent.setup();
    const { onChange, input } = renderInput(field({ min: 1 }), 2);

    await user.clear(input);
    await user.type(input, "30");
    await user.tab();

    expect(onChange).toHaveBeenLastCalledWith(30);
  });

  it("falls back to min when the user clears the field", async () => {
    const user = userEvent.setup();
    const { onChange, input } = renderInput(field({ min: 1 }), 2);

    await user.clear(input);
    await user.tab();

    expect(onChange).toHaveBeenLastCalledWith(1);
  });

  it("clamps a typed value below min", async () => {
    const user = userEvent.setup();
    const { onChange, input } = renderInput(field({ min: 1 }), 5);

    await user.clear(input);
    await user.type(input, "0");
    await user.tab();

    expect(onChange).toHaveBeenLastCalledWith(1);
  });

  it("clamps a typed value above max", async () => {
    const user = userEvent.setup();
    const { onChange, input } = renderInput(field({ min: 1, max: 10 }), 5);

    await user.clear(input);
    await user.type(input, "99");
    await user.tab();

    expect(onChange).toHaveBeenLastCalledWith(10);
  });
});


/* ── What the field DISPLAYS, not just what onChange received ───────────────
 *
 * Found by an independent mutation audit (Phase 2). Every test above asserts
 * on the onChange spy; none asserted what the input actually renders. Two
 * mutations survived the whole frontend suite behind that.
 *
 * These use a CONTROLLED harness, because the one above is not: it passes a
 * fixed `value` that never updates, so it cannot see a divergence between the
 * committed value and the displayed one. Production is controlled —
 * SettingsPage holds the value in state and feeds it back — and the bug only
 * exists there.
 */

function ControlledInput({ f, initial }) {
  const [value, setValue] = useState(initial);
  return (
    <ThemeProvider>
      <SettingInput field={f} value={value} onChange={setValue} />
    </ThemeProvider>
  );
}

function renderControlled(f, initial) {
  render(<ControlledInput f={f} initial={initial} />);
  return screen.getByLabelText(f.label);
}

describe("IntegerInput — what the user sees after blur", () => {
  it("displays the clamped value, not the out-of-range text that was typed", async () => {
    /**
     * setDraft(null) on blur is what hands rendering back to the committed
     * value. Without it the draft string outlives the blur: onChange
     * correctly receives 10, so the spy-only test passes, while the field
     * keeps showing "99". The user is looking at a number that is not the one
     * that will be saved — and Save is enabled, so they save 10 believing it
     * is 99.
     */
    const user = userEvent.setup();
    const input = renderControlled(field({ min: 1, max: 10, label: "L" }), 5);

    await user.clear(input);
    await user.type(input, "99");
    await user.tab();

    expect(input).toHaveValue(10);
  });

  it("displays the min after being clamped up from below it", async () => {
    const user = userEvent.setup();
    const input = renderControlled(field({ min: 2, label: "L" }), 5);

    await user.clear(input);
    await user.type(input, "1");
    await user.tab();

    expect(input).toHaveValue(2);
  });

  it("displays the fallback after the field is cleared and blurred", async () => {
    const user = userEvent.setup();
    const input = renderControlled(field({ min: 2, label: "L" }), 5);

    await user.clear(input);
    await user.tab();

    expect(input).toHaveValue(2);
  });

  it("displays an in-range typed value unchanged", async () => {
    /** The clamp must not fire when nothing is out of range. */
    const user = userEvent.setup();
    const input = renderControlled(field({ min: 1, max: 100, label: "L" }), 5);

    await user.clear(input);
    await user.type(input, "30");
    await user.tab();

    expect(input).toHaveValue(30);
  });

  it("lets a value be typed digit by digit through a min it passes under", async () => {
    /**
     * The reason clamping happens on blur rather than per keystroke. With a
     * min of 2, typing "10" goes "1" → clamped to 2 → field reads "2" → the
     * next keystroke gives "20". 10 was unreachable. This is that scenario as
     * a DOM assertion rather than a spy one.
     */
    const user = userEvent.setup();
    const input = renderControlled(field({ min: 2, label: "L" }), 5);

    await user.clear(input);
    await user.type(input, "10");

    expect(input).toHaveValue(10);

    await user.tab();
    expect(input).toHaveValue(10);
  });
});

describe("IntegerInput — onChange fires while typing", () => {
  it("reports each keystroke, not only the blur", async () => {
    /**
     * The source comment says onChange fires per keystroke so the dirty
     * indicator and Save button track what is on screen. Every existing
     * assertion uses toHaveBeenLastCalledWith, which is satisfied by the
     * blur-time call alone — so the stated behaviour was unpinned, and
     * removing it would leave Save greyed out while the field shows an edit.
     */
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ThemeProvider>
        <SettingInput field={field({ min: 1, label: "L" })} value={5} onChange={onChange} />
      </ThemeProvider>,
    );
    const input = screen.getByLabelText("L");

    await user.clear(input);
    await user.type(input, "12");

    expect(onChange.mock.calls.map((c) => c[0])).toEqual([1, 12]);
  });

  it("does not report a keystroke that leaves the field non-numeric", async () => {
    /** Clearing the field parses to NaN — committing that would write junk
     *  into the settings payload on every backspace. */
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ThemeProvider>
        <SettingInput field={field({ min: 1, label: "L" })} value={5} onChange={onChange} />
      </ThemeProvider>,
    );

    await user.clear(screen.getByLabelText("L"));

    expect(onChange).not.toHaveBeenCalled();
  });
});
