/**
 * Cross-setting warning: Always Ask on subtitles with undefined subtitles
 * not kept.
 *
 * Always Ask deliberately does not resolve the language — that is what
 * asking means. So the track reaches the keep/drop filter still reading
 * "und", matches nothing in Keep Subtitle Languages, and is dropped before
 * the flagging pass can see it. The user is asked nothing and the subtitle
 * is gone.
 *
 * Verified against analyze_file rather than reasoned about: an ordinary
 * undefined subtitle is dropped, a forced one is kept and flagged. Hence
 * the wording, and hence the "only forced tracks survive" clause.
 *
 * The combination became reachable when the undefined-language setting was
 * split per track type: "fix audio automatically, ask me about subtitles"
 * is the configuration the split exists to allow, and it is exactly the
 * one that silently does nothing.
 *
 * Verified by mutation, 4 applied, 4 killed:
 *
 *   • The warning removed entirely                        → killed
 *   • Shown regardless of keep_undefined_subtitles        → killed
 *   • Shown for always_fix as well as always_ask          → killed
 *   • Attached to the audio key instead of the subtitle one → killed
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";

const SCHEMA = [
  { key: "fix_undefined_language", label: "Fix Undefined Subtitle Language Tags",
    type: "select", group: "Metadata",
    options: [
      { value: "always_fix",   label: "Always fix" },
      { value: "always_ask",   label: "Always ask" },
      { value: "always_leave", label: "Always leave" },
    ] },
  { key: "fix_undefined_language_audio", label: "Fix Undefined Audio Language Tags",
    type: "select", group: "Metadata",
    options: [
      { value: "always_fix",   label: "Always fix" },
      { value: "always_ask",   label: "Always ask" },
      { value: "always_leave", label: "Always leave" },
    ] },
  { key: "keep_undefined_subtitles",
    label: "Always Keep Undefined-Language Subtitles",
    type: "boolean", group: "Subtitles" },
];

function mockApi(values) {
  vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
    const u = String(url);
    if (opts.method === "PUT") return { ok: true, json: async () => ({}) };
    if (u.includes("/api/settings/schema")) {
      return { ok: true, json: async () => SCHEMA };
    }
    if (u.match(/\/api\/settings\/?$/)) {
      return { ok: true, json: async () => values };
    }
    return { ok: true, json: async () => ({ value: null, count: 0 }) };
  }));
}

async function renderPage(values) {
  mockApi(values);
  localStorage.setItem("remuxarr.settingsCategory", "processing");
  render(
    <ThemeProvider>
      <SettingsPage api="" toast={() => {}} />
    </ThemeProvider>,
  );
  await screen.findByText("Fix Undefined Subtitle Language Tags");
}

const warning = () => screen.queryByText(/will not reach you/);

describe("SettingsPage cross-setting warnings", () => {
  beforeEach(() => localStorage.clear());

  it("warns when Always Ask is set and undefined subtitles are not kept", async () => {
    await renderPage({
      fix_undefined_language: "always_ask",
      fix_undefined_language_audio: "always_leave",
      keep_undefined_subtitles: false,
    });

    expect(warning()).toBeTruthy();
  });

  it("does not warn once undefined subtitles are kept", async () => {
    /* The combination is fine here: the track survives keep/drop with its
     * und tag and does reach review. */
    await renderPage({
      fix_undefined_language: "always_ask",
      fix_undefined_language_audio: "always_leave",
      keep_undefined_subtitles: true,
    });

    expect(warning()).toBeNull();
  });

  it("does not warn for Always Fix, which resolves the tag itself", async () => {
    /* always_fix resolves und to the primary language BEFORE keep/drop, so
     * the track is judged on the corrected tag and keep_undefined_subtitles
     * never comes into it. Warning here would be noise on a correct setup,
     * which is how warnings get ignored. */
    await renderPage({
      fix_undefined_language: "always_fix",
      fix_undefined_language_audio: "always_leave",
      keep_undefined_subtitles: false,
    });

    expect(warning()).toBeNull();
  });

  it("does not warn when it is AUDIO that is set to Always Ask", async () => {
    /* keep_undefined_subtitles has nothing to do with audio: an undefined
     * audio track is kept either way. Attaching this warning to the audio
     * key would tell the user to change a subtitle setting to fix an audio
     * one. */
    await renderPage({
      fix_undefined_language: "always_leave",
      fix_undefined_language_audio: "always_ask",
      keep_undefined_subtitles: false,
    });

    expect(warning()).toBeNull();
  });
});
