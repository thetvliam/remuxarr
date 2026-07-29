import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtSize, fmtDur, fmtRel } from "../../utils";
import { LED } from "../atoms/LED";
import { Stat } from "../atoms/Stat";
import { SegBar } from "../bars/SegBar";

/* ═══════════════════════════════════════════════════════════════════════════
 * ACTIVE WORKER PANEL  (top strip — always visible on dashboard)
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const ActivePanel = ({ job, isMobile = false, onAbort, transitioning = false }) => {
  const { palette, type, space, radius, legacy } = useTheme();

  if (!job && !transitioning) {
    return (
      <div style={{
        padding: `${space.xl}px ${space.huge}px`,
        background: palette.card,
        borderBottom: `1px solid ${palette.border}`,
        display: "flex",
        alignItems: "center",
        gap: space.lg,
      }}>
      <LED color={palette.dim} size={legacy.ledSizeLg} />
      <span style={{
        color: palette.dim,
        fontSize: type.size.base,
        letterSpacing: type.tracking.snug,
      }}>
      WORKER IDLE — no active job
      </span>
      </div>
    );
  }

  // Brief gap between jobs — worker isn't paused and the queue still has
  // pending items, so another job is about to start any moment. Renders
  // the exact same structural layout as the real processing state below
  // (same rows, same padding), just with placeholder content instead of
  // real job data. Without this, the panel's height collapses to the
  // short idle strip above for that gap, pushing the queue/history
  // panels below it up and down on every single job transition.
  const f   = transitioning ? {} : (job.file || {});
  const pct = transitioning ? 0  : (job.progress || 0);

  return (
    <div style={{
      padding: `${legacy.activePadY}px ${space.huge}px`,
      background: palette.card,
      borderBottom: `1px solid ${palette.border}`,
      borderLeft: `${legacy.accentWidth}px solid ${transitioning ? palette.dim : palette.amber}`,
    }}>
    {/* Row 1 — status labels */}
    <div style={{ display: "flex", alignItems: "center", gap: space.md, marginBottom: space.md }}>
    <LED
    color={transitioning ? palette.dim : palette.amber}
    pulse={!transitioning}
    size={legacy.ledSizeLg}
    />
    <span style={{
      color: palette.dim,
      fontSize: type.size.xs,
      letterSpacing: type.tracking.max,
      fontWeight: type.weight.bold,
    }}>
    {transitioning ? "PICKING UP NEXT ITEM…" : "PROCESSING"}
    </span>
    {!transitioning && job.is_dry_run && (
      <span style={{
        padding: `${legacy.badgePadY}px ${legacy.badgePadX}px`,
        background: legacy.dryRunBg,
        border: `1px solid ${alpha(palette.yellow, ALPHA.heavy)}`,
                                          borderRadius: legacy.badgeRadius,
                                          color: palette.yellow,
                                          fontSize: type.size.xs,
                                          letterSpacing: type.tracking.wide,
      }}>
      DRY RUN
      </span>
    )}
    <span style={{ marginLeft: "auto", color: palette.muted, fontSize: type.size.md }}>
    {transitioning ? "—" : (job.current_action || "—")}
    </span>
    {!transitioning && onAbort && (
      <button
      onClick={() => onAbort(job.id)}
      title="Cancel this file and pause auto-start"
      style={{
        padding: `${legacy.abortPadY}px ${legacy.abortPadX}px`,
        background: "transparent",
        border: `1px solid ${palette.red}`,
        borderRadius: radius.sm,
        color: palette.red,
        fontSize: type.size.xs,
        fontFamily: type.family,
        fontWeight: type.weight.bold,
        letterSpacing: type.tracking.wide,
        cursor: "pointer",
        flexShrink: 0,
      }}
      >
      ■ ABORT
      </button>
    )}
    </div>

    {/* Row 2 — filename */}
    <div style={{
      color: transitioning ? palette.dim : palette.text,
      fontSize: type.size.xl,
      fontWeight: type.weight.semibold,
      marginBottom: space.xxs,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    }}>
    {transitioning ? "Waiting for the next queued file…" : (f.filename || "Unknown file")}
    </div>

    {/* Row 3 — path (hidden on mobile — filename is enough) */}
    {!isMobile && (
      <div style={{
        color: palette.dim,
        fontSize: type.size.md,
        marginBottom: space.lg,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}>
      {transitioning ? "" : (f.path || "")}
      </div>
    )}

    {/* Progress bar */}
    <div style={{ marginTop: isMobile ? space.sm : 0, marginBottom: 0 }}>
    <SegBar value={pct} />
    </div>

    {/* Row 4 — stats: all 5 on desktop, PROGRESS + SIZE only on mobile */}
    <div style={{ display: "flex", gap: space.max, marginTop: space.md }}>
    <Stat label="PROGRESS"  value={transitioning ? "—" : `${pct.toFixed(1)}%`} color={transitioning ? palette.dim : palette.amber} />
    <Stat label="SIZE"      value={transitioning ? "—" : fmtSize(f.size)} />
    {!isMobile && <Stat label="DURATION"  value={transitioning ? "—" : fmtDur(f.duration)} />}
    {!isMobile && <Stat label="CONTAINER" value={transitioning ? "—" : ((f.container || "").toUpperCase() || "—")} />}
    {!isMobile && <Stat label="STARTED"   value={transitioning ? "—" : fmtRel(job.started_at)} />}
    </div>
    </div>
  );
};
