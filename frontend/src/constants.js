/* ═══════════════════════════════════════════════════════════════════════════
 * CONSTANTS
 *
 * Colour values moved to theme.js as `palette`. `C` is re-exported from
 * there unchanged, so every existing `import { C } from "../constants"`
 * keeps working while components migrate to the theme tokens one directory
 * at a time. Nothing needs to change in a file until its turn comes.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */

import { palette } from "./theme";

// Derive the API base from whatever URL the page was loaded from.
// This means it works correctly whether you access Remuxarr via IP, hostname,
// or through a reverse proxy — no hardcoded localhost that only works locally.
export const DEFAULT_API = `${window.location.protocol}//${window.location.host}`;

// Back-compat alias. New code should prefer `palette` from theme.js.
export const C = palette;

export const STATUS_COLOR = {
  pending:        C.dim,
  processing:     C.blue,
  success:        C.green,
  failed:         C.red,
  manual_review:  C.yellow,
  skipped:        C.dim,
  cancelled:      C.dim,
  dry_run:        C.violet,
};

export const ACTION_CFG = {
  copy_track:         { bg: "#091a0f", border: "#122a1a", text: C.green,  label: "COPY"      },
  drop_track:         { bg: "#1a0909", border: "#2a1212", text: C.red,    label: "DROP"      },
  transcode_track:    { bg: "#1a1200", border: "#2a1e00", text: C.amber,  label: "TRANSCODE" },
  change_container:   { bg: "#090f1a", border: "#12182a", text: C.blue,   label: "CONVERT"   },
  flag_manual_review: { bg: "#1a1000", border: "#2a1c00", text: C.yellow, label: "FLAG"      },
  extract_subtitle:   { bg: "#001a1a", border: "#0f2a2a", text: "#2dd4d4", label: "EXTRACT"   },
  add_faststart:      { bg: "#0d001a", border: "#1e0a2a", text: C.violet, label: "FASTSTART" },
};
