import { useTheme } from "../../theme";
import { fmtSize, fmtDur } from "../../utils";
import { LED } from "../atoms/LED";
import { Stat } from "../atoms/Stat";
import { SegBar } from "../bars/SegBar";

export const ForgeActivePanel = ({ job, workerPaused = false }) => {
    const { palette, type, space, size } = useTheme();
    if (!job) return (
        <div style={{
            padding: `${space.xl}px ${space.huge}px`, background: palette.card,
            borderBottom: `1px solid ${palette.border}`,
            display: "flex", alignItems: "center", gap: space.lg,
        }}>
        <LED color={palette.dim} size={size.ledSizeLg} />
        <span style={{ color: palette.dim, fontSize: type.size.base, letterSpacing: type.tracking.snug }}>
        FORGE IDLE — select a file from the candidates list to add an AC3 5.1 track
        </span>
        </div>
    );

    const f   = job.file || {};
    const pct = job.progress || 0;

    // /api/forge/active deliberately returns pending jobs as well as processing
    // ones, so that clicking "add" shows something immediately rather than
    // nothing. This panel previously ignored job.status and rendered every one
    // as FORGING with a pulsing LED and a 0.0% bar — which is accurate for a
    // job the worker has actually picked up, and actively misleading for one it
    // has not. With the worker paused nothing will pick it up at all, so the
    // panel sat claiming work was in progress indefinitely.
    const queued = job.status === "pending" || job.status === "undo_pending";

    // Why it is waiting decides what the user should do about it: paused means
    // press Resume, otherwise the worker is simply busy and it will start on
    // its own. Saying "queued" without saying which leaves them guessing.
    const waitReason = workerPaused
    ? "Worker is paused — press Resume to start this job"
    : "Waiting for a free worker slot";

    const accent = queued ? palette.amber : palette.blue;

    return (
        <div style={{
            padding: `${space.xl}px ${space.huge}px`, background: palette.card,
            borderBottom: `1px solid ${palette.border}`,
            borderLeft: `${size.accentWidth}px solid ${accent}`,
        }}>
        <div style={{ display: "flex", alignItems: "center", gap: space.md, marginBottom: space.md }}>
        <LED color={accent} pulse={!queued} size={size.ledSizeLg} />
        <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
        {queued ? "QUEUED" : (job.is_undo ? "UNDOING" : "FORGING")}
        </span>
        <span style={{ marginLeft: "auto", color: queued ? palette.amber : palette.muted, fontSize: type.size.md }}>
        {queued
            ? waitReason
            : (job.current_action || (job.is_undo ? "Removing AC3 5.1 track" : "Adding AC3 5.1 track"))}
            </span>
            </div>

            <div style={{
                color: palette.text, fontSize: type.size.xl, fontWeight: type.weight.semibold, marginBottom: space.xxs,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
            {f.filename || "Unknown file"}
            </div>
            <div style={{
                color: palette.dim, fontSize: type.size.md, marginBottom: space.lg,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
            {f.path || ""}
            </div>

            {/* A progress bar on a job that has not started reads as "stuck at 0%". */}
            {!queued && <SegBar value={pct} />}

            <div style={{ display: "flex", gap: space.max, marginTop: space.md }}>
            <Stat
            label={queued ? "STATUS" : "PROGRESS"}
            value={queued ? (workerPaused ? "Paused" : "Queued") : `${pct.toFixed(1)}%`}
            color={accent}
            />
            <Stat label="SIZE"     value={fmtSize(f.size)} />
            <Stat label="DURATION" value={fmtDur(f.duration)} />
            <Stat label="ACTION"   value={job.is_undo ? "Removing AC3" : "Adding AC3"} color={job.is_undo ? palette.red : palette.amber} />
            </div>
            </div>
    );
};
