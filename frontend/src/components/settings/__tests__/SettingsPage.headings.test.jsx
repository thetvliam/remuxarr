/**
 * SettingsPage — every page's section headings.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The action sections on Settings each drew their own heading, and they had
 * drifted from the schema groups' SectionHeader. Backup & Restore and Full
 * Database Backup & Restore were grey where every other heading is amber,
 * and the Backup and Maintenance pages opened with a margin and a rule above
 * their first heading: a separator written for the single long page those
 * sections were built on, which put the heading 65px below the save bar
 * where every other page has it 6px below. They all use SectionHeader now.
 * Nothing pinned any of it.
 *
 * Pinned here, page by page: the headings in order, found the way a screen
 * reader finds them (role heading, level 2, which none of them had before);
 * the first one's small top gap against the later ones' larger gap, which
 * is the page telling its first section that it opens the page; and the
 * colour, amber for all but Danger Zone. Also that Full Backup keeps its
 * heading once a restore asks for a restart, where the whole section,
 * heading and all, used to give way to the notice.
 *
 * On the old code every test here fails for want of the heading role, which
 * says nothing about gaps or colours; the evidence for those is the mutants,
 * each putting one old behaviour back into the new code. Verified by
 * mutation, 11 applied, 11 killed, every one surviving the whole suite
 * without this file:
 *   • Backup's, Maintenance's or Appearance's first section not told it is
 *     first, and Full Backup told it is when it is not (four)
 *   • SectionHeader's first and later gaps swapped
 *   • Backup & Restore or Full Database Backup & Restore grey again, and
 *     Danger Zone's heading not red (three)
 *   • Application Logs back to a hand-written heading of its own
 *   • the headings losing their role
 *   • the restart notice dropping the heading
 * The Full Backup one first survived: only the sections that open a page
 * took `first`, so passing it to Full Backup did nothing. Every section now
 * takes it, so the page alone decides, and that mutant is killed.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../SettingsPage";
import { FullBackupSection } from "../FullBackupSection";
import { ThemeProvider, useTheme } from "../../../theme";

/* Two schema groups, so the Integrations page has a heading after its
 * first, the same as each action page. */
const SCHEMA = [
  { key: "sonarr_url", label: "Sonarr URL", type: "string", group: "Sonarr" },
  { key: "radarr_url", label: "Radarr URL", type: "string", group: "Radarr" },
];
const VALUES = { sonarr_url: "", radarr_url: "" };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = String(url);
    const body =
      u.includes("/api/settings/schema") ? SCHEMA
      : u.match(/\/api\/settings\/?$/) ? VALUES
      : u.includes("/api/logs") ? { records: [] }
      : u.includes("/api/backup/import") ? { success: true, previous_database_backup: "/config/before.db" }
      : { value: null, count: 0 };
    return { ok: true, status: 200, json: async () => body };
  }));
});

/** The theme the page is drawn with, read from inside the provider. */
let theme;
const Probe = () => { theme = useTheme(); return null; };

/** What jsdom reads back for `value` set on `prop`. */
const css = (prop, value) => {
  const el = document.createElement("div");
  el.style[prop] = value;
  return el.style[prop];
};

/** Let every load the page started on mount finish inside act. */
const settle = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });

const renderCategory = async (category) => {
  localStorage.setItem("remuxarr.settingsCategory", category);
  render(
    <ThemeProvider>
      <Probe />
      <SettingsPage api="" toast={() => {}} />
    </ThemeProvider>,
  );
  await settle();
};

const headings = () => screen.getAllByRole("heading", { level: 2 });

describe("SettingsPage — section headings", () => {
  it.each([
    ["integrations", ["SONARR", "RADARR"]],
    ["maintenance", ["MAINTENANCE", "APPLICATION LOGS"]],
    ["backup", ["BACKUP & RESTORE", "FULL DATABASE BACKUP & RESTORE", "DANGER ZONE"]],
    ["appearance", ["THEME"]],
  ])("the %s page: its headings in order, the first close under the save bar", async (category, names) => {
    await renderCategory(category);
    if (category === "integrations") await screen.findByText("Sonarr URL");

    const found = headings();
    expect(found.map(h => h.textContent)).toEqual(names);
    expect(found.map(h => h.style.marginTop)).toEqual(
      names.map((_, i) => `${i === 0 ? theme.space.xxs : theme.space.xxxl}px`));
  });

  it.each(["integrations", "maintenance", "backup", "appearance"])(
    "the %s page: every heading amber but Danger Zone's, which is red", async (category) => {
      await renderCategory(category);
      if (category === "integrations") await screen.findByText("Sonarr URL");

      for (const h of headings()) {
        const want = h.textContent === "DANGER ZONE" ? theme.palette.red : theme.palette.amber;
        expect([h.textContent, h.firstChild.style.color]).toEqual([h.textContent, css("color", want)]);
      }
    });
});

describe("FullBackupSection — after a restore", () => {
  it("keeps its heading above the restart notice", async () => {
    render(
      <ThemeProvider>
        <FullBackupSection api="" toast={() => {}} />
      </ThemeProvider>,
    );
    const file = new File(["PK"], "backup.zip", { type: "application/zip" });
    fireEvent.change(document.querySelector('input[type="file"]'), { target: { files: [file] } });
    fireEvent.change(screen.getByPlaceholderText(/to confirm/), { target: { value: "REPLACE DATABASE" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "REPLACE DATABASE" })); });

    expect(await screen.findByText("RESTART REQUIRED")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "FULL DATABASE BACKUP & RESTORE" })).toBeInTheDocument();
  });
});
