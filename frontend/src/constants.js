/* ═══════════════════════════════════════════════════════════════════════════
 * CONSTANTS
 *
 * Visual values live in theme.jsx and are read through useTheme(). This file
 * previously also re-exported the default theme's palette, status colours and
 * action config as static values, so that not-yet-migrated components kept
 * working. Those exports were frozen — they did not follow a theme switch —
 * and every file still importing them stayed on the default theme while the
 * rest of the app changed around it.
 *
 * Migration is complete, so they are gone. Their absence is load-bearing: if
 * a static visual export reappears here, whatever imports it silently stops
 * responding to the theme.
 ═══════════════════════════════════════════════════════════════════════════ */

 // Derive the API base from whatever URL the page was loaded from.
 // This means it works correctly whether you access Remuxarr via IP, hostname,
 // or through a reverse proxy — no hardcoded localhost that only works locally.
 export const DEFAULT_API = `${window.location.protocol}//${window.location.host}`;

/* How long a two-click destructive confirmation stays armed before disarming
 * itself. Shared rather than repeated so the five sites cannot drift apart
 * again: they had settled at 3s in QueuePanel and Maintenance and 4s in
 * DangerZone and the recycle bin, with nothing marking either as the intended
 * one.
 *
 * 4s, for two reasons. It is what the two highest-stakes actions — clearing
 * the database, and permanently deleting recycle-bin backups — already used,
 * so standardising there moves the others toward more time to reconsider
 * rather than less. And 3s is short for reading a confirm label and deciding,
 * particularly on a phone.
 *
 * Deliberately NOT tiered by how destructive the action is. A timeout is
 * invisible until it fires, so it cannot tell anyone this button is the
 * dangerous one; the label and the colour do that. Varying it would only make
 * the safe actions harder to confirm.
 *
 * Not in theme.jsx: this is interaction timing, not a visual value, and a
 * theme must not be able to change how long a destructive action stays armed. */
export const CONFIRM_MS = 4000;

/* The one documented exception. Importing settings asks the user to read a
 * filename and decide whether to overwrite every setting they have, which is
 * not a four-second decision — and lapsing there is unusually annoying,
 * because re-picking the same file in a file input fires no change event, so
 * the button looks broken until they choose a different one. */
export const IMPORT_CONFIRM_MS = 10000;
