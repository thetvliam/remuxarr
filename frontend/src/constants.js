/* ═══════════════════════════════════════════════════════════════════════════
 * CONSTANTS
 *
 * Visual values now live in theme.jsx. The exports below are the DEFAULT
 * theme's values, kept so components that have not yet been migrated keep
 * working unchanged. They are static: they do NOT follow a theme switch.
 * That is the reason migration needs to be finished rather than left
 * half-done — anything still importing from here stays on the default
 * theme while the rest of the app changes around it.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */

import { themes, DEFAULT_THEME_ID } from "./theme";

const base = themes[DEFAULT_THEME_ID];

// Derive the API base from whatever URL the page was loaded from.
// This means it works correctly whether you access Remuxarr via IP, hostname,
// or through a reverse proxy — no hardcoded localhost that only works locally.
export const DEFAULT_API = `${window.location.protocol}//${window.location.host}`;

export const C            = base.palette;
export const STATUS_COLOR = base.statusColor;
export const ACTION_CFG   = base.actionCfg;
