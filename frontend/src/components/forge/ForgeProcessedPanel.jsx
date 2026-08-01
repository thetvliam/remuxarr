import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtSize, fmtRel } from "../../utils";
import { LED } from "../atoms/LED";
import { StatusBadge } from "../atoms/StatusBadge";
import { EmptyState } from "../atoms/EmptyState";
import { PanelHeader } from "../layout/PanelHeader";

// ── Processed panel ────────────────────────────────────────────────────────

export const ForgeProcessedPanel = ({ jobs, onUndo }) => (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
    <PanelHeader label="PROCESSED" count={jobs.length} />
    <div style={{ flex: 1, overflowY: "auto" }}>
    {jobs.length === 0 ? (
        <EmptyState msg="No files processed yet — add AC3 to a candidate to get started" />
    ) : (
        jobs.map(j => <ForgeProcessedRow key={j.id} job={j} onUndo={onUndo} />)
    )}
    </div>
    </div>
);

const ForgeProcessedRow = ({ job, onUndo }) => {
    const { palette, type, space, radius, size, surface } = useTheme();
    const [hover, setHover] = useState(false);
    const f = job.file || {};

    const sizeDiff = job.output_size && job.original_size
    ? job.output_size - job.original_size : null;

    const isUndoPending = job.status === "undo_pending";
    const isFailed      = job.status === "failed";
    const isUndoFailed  = job.status === "undo_failed";

    return (
        <div
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
            display: "flex",
            alignItems: "center",
            gap: space.md,
            padding: `${space.md}px ${space.xl}px`,
            background: hover ? surface.rowHoverBg : "transparent",
            borderBottom: `1px solid ${palette.border}`,
            transition: "background 0.1s",
        }}
        >
        <LED
        color={
            isUndoPending ? palette.blue
            : isFailed || isUndoFailed ? palette.red
            : palette.green
        }
        pulse={isUndoPending}
        size={size.ledSizeSm}
        />

        {/* File info */}
        <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
            color: palette.text, fontSize: type.size.base, fontWeight: type.weight.medium,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            marginBottom: space.hair,
        }}>
        {f.filename || "—"}
        </div>
        <div style={{ display: "flex", gap: space.md, alignItems: "center" }}>
        {/* Status badge */}
        <StatusBadge status={job.status} />

        {/* Size delta */}
        {sizeDiff !== null && (
            <span style={{ color: palette.muted, fontSize: type.size.sm }}>
            {fmtSize(job.original_size)}
            <span style={{ color: palette.dim }}> → </span>
            {fmtSize(job.output_size)}
            <span style={{
                color: palette.amber,
                marginLeft: space.xxs,
            }}>
            (+{fmtSize(Math.abs(sizeDiff))})
            </span>
            </span>
        )}

        {/* Error excerpt */}
        {(isFailed || isUndoFailed) && job.error_message && (
            <span style={{
                color: palette.red, fontSize: type.size.sm,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
            {job.error_message.slice(0, 60)}
            </span>
        )}

        {!sizeDiff && !isFailed && !isUndoFailed && (
            <span style={{ color: palette.dim, fontSize: type.size.sm }}>{fmtRel(job.completed_at)}</span>
        )}
        </div>
        </div>

        {/* Undo button — only for success and undo_failed */}
        {(job.status === "success" || job.status === "undo_failed") && (
            <button
            onClick={() => onUndo(job.id)}
            style={{
                padding: `${space.xxs}px ${space.lg}px`,
                flexShrink: 0,
                background: hover ? alpha(palette.red, ALPHA.low) : "transparent",
                border: `1px solid ${hover ? palette.red : palette.border}`,
                borderRadius: radius.sm,
                color: hover ? palette.red : palette.dim,
                fontSize: type.size.xs,
                fontFamily: type.family,
                fontWeight: type.weight.bold,
                letterSpacing: type.tracking.wide,
                cursor: "pointer",
                transition: "all 0.15s",
                whiteSpace: "nowrap",
            }}
            >
            {job.status === "undo_failed" ? "↺ RETRY UNDO" : "↺ UNDO AC3"}
            </button>
        )}

        {/* Undo pending indicator */}
        {isUndoPending && (
            <span style={{
                color: palette.blue, fontSize: type.size.xs, fontFamily: type.family,
                letterSpacing: type.tracking.wide, flexShrink: 0,
            }}>
            REMOVING…
            </span>
        )}
        </div>
    );
};
