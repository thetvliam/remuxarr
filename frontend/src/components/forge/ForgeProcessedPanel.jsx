import { useState } from "react";
import { C } from "../../constants";
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
            gap: 10,
            padding: "9px 14px",
            background: hover ? "#ffffff07" : "transparent",
            borderBottom: `1px solid ${C.border}`,
            transition: "background 0.1s",
        }}
        >
        <LED
        color={
            isUndoPending ? C.blue
            : isFailed || isUndoFailed ? C.red
            : C.green
        }
        pulse={isUndoPending}
        size={6}
        />

        {/* File info */}
        <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
            color: C.text, fontSize: 12, fontWeight: 500,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            marginBottom: 2,
        }}>
        {f.filename || "—"}
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        {/* Status badge */}
        <StatusBadge status={job.status} />

        {/* Size delta */}
        {sizeDiff !== null && (
            <span style={{ color: C.muted, fontSize: 10 }}>
            {fmtSize(job.original_size)}
            <span style={{ color: C.dim }}> → </span>
            {fmtSize(job.output_size)}
            <span style={{
                color: C.amber,
                marginLeft: 4,
            }}>
            (+{fmtSize(Math.abs(sizeDiff))})
            </span>
            </span>
        )}

        {/* Error excerpt */}
        {(isFailed || isUndoFailed) && job.error_message && (
            <span style={{
                color: C.red, fontSize: 10,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
            {job.error_message.slice(0, 60)}
            </span>
        )}

        {!sizeDiff && !isFailed && !isUndoFailed && (
            <span style={{ color: C.dim, fontSize: 10 }}>{fmtRel(job.completed_at)}</span>
        )}
        </div>
        </div>

        {/* Undo button — only for success and undo_failed */}
        {(job.status === "success" || job.status === "undo_failed") && (
            <button
            onClick={() => onUndo(job.id)}
            style={{
                padding: "4px 12px",
                flexShrink: 0,
                background: hover ? C.red + "18" : "transparent",
                border: `1px solid ${hover ? C.red : C.border}`,
                color: hover ? C.red : C.dim,
                fontSize: 9,
                fontFamily: "inherit",
                fontWeight: 700,
                letterSpacing: "0.1em",
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
                color: C.blue, fontSize: 9, fontFamily: "inherit",
                letterSpacing: "0.1em", flexShrink: 0,
            }}>
            REMOVING…
            </span>
        )}
        </div>
    );
};
