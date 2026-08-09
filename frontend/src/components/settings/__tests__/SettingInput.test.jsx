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
