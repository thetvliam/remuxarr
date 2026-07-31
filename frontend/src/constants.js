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
 ═ ══════════════════════════════════════════════════════════════════════════ */

 // Derive the API base from whatever URL the page was loaded from.
 // This means it works correctly whether you access Remuxarr via IP, hostname,
 // or through a reverse proxy — no hardcoded localhost that only works locally.
 export const DEFAULT_API = `${window.location.protocol}//${window.location.host}`;
