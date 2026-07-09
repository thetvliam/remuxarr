import { C } from "../../constants";
import { fmtSize, fmtDur, fmtRel } from "../../utils";
import { LED } from "../atoms/LED";
import { Stat } from "../atoms/Stat";
import { SegBar } from "../bars/SegBar";

/* ═══════════════════════════════════════════════════════════════════════════
   ACTIVE WORKER PANEL  (top strip — always visible on dashboard)
═══════════════════════════════════════════════════════════════════════════ */
export const ActivePanel = ({ job, isMobile = false, onAbort, transitioning = false }) => {
  if (!job && !transitioning) {
    return (
      <div style={{
        padding: "16px 24px",
        background: C.card,
        borderBottom: `1px solid ${C.border}`,
        display: "flex",
        alignItems: "center",
        gap: 12,
      }}>
        <LED color={C.dim} size={8} />
        <span style={{ color: C.dim, fontSize: 12, letterSpacing: "0.06em" }}>
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
      padding: "14px 24px",
      background: C.card,
      borderBottom: `1px solid ${C.border}`,
      borderLeft: `3px solid ${transitioning ? C.dim : C.amber}`,
    }}>
      {/* Row 1 — status labels */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <LED color={transitioning ? C.dim : C.amber} pulse={!transitioning} size={8} />
        <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
          {transitioning ? "PICKING UP NEXT ITEM…" : "PROCESSING"}
        </span>
        {!transitioning && job.is_dry_run && (
          <span style={{
            padding: "1px 6px",
            background: "#1a1400",
            border: `1px solid ${C.yellow}55`,
            color: C.yellow,
            fontSize: 9,
            letterSpacing: "0.1em",
          }}>
            DRY RUN
          </span>
        )}
        <span style={{ marginLeft: "auto", color: C.muted, fontSize: 11 }}>
          {transitioning ? "—" : (job.current_action || "—")}
        </span>
        {!transitioning && onAbort && (
          <button
            onClick={() => onAbort(job.id)}
            title="Cancel this file and pause auto-start"
            style={{
              padding: "3px 11px",
              background: "transparent",
              border: `1px solid ${C.red}`,
              color: C.red,
              fontSize: 9,
              fontFamily: "inherit",
              fontWeight: 700,
              letterSpacing: "0.1em",
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
        color: transitioning ? C.dim : C.text,
        fontSize: 14,
        fontWeight: 600,
        marginBottom: 4,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}>
        {transitioning ? "Waiting for the next queued file…" : (f.filename || "Unknown file")}
      </div>

      {/* Row 3 — path (hidden on mobile — filename is enough) */}
      {!isMobile && (
        <div style={{
          color: C.dim,
          fontSize: 11,
          marginBottom: 12,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {transitioning ? "" : (f.path || "")}
        </div>
      )}

      {/* Progress bar */}
      <div style={{ marginTop: isMobile ? 8 : 0, marginBottom: 0 }}>
        <SegBar value={pct} />
      </div>

      {/* Row 4 — stats: all 5 on desktop, PROGRESS + SIZE only on mobile */}
      <div style={{ display: "flex", gap: 28, marginTop: 10 }}>
        <Stat label="PROGRESS"  value={transitioning ? "—" : `${pct.toFixed(1)}%`} color={transitioning ? C.dim : C.amber} />
        <Stat label="SIZE"      value={transitioning ? "—" : fmtSize(f.size)} />
        {!isMobile && <Stat label="DURATION"  value={transitioning ? "—" : fmtDur(f.duration)} />}
        {!isMobile && <Stat label="CONTAINER" value={transitioning ? "—" : ((f.container || "").toUpperCase() || "—")} />}
        {!isMobile && <Stat label="STARTED"   value={transitioning ? "—" : fmtRel(job.started_at)} />}
      </div>
    </div>
  );
};
