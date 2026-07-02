import { C } from "../../constants";
import { fmtSize, fmtDur } from "../../utils";
import { LED } from "../atoms/LED";
import { Stat } from "../atoms/Stat";
import { SegBar } from "../bars/SegBar";

export const ForgeActivePanel = ({ job }) => {
    if (!job) return (
        <div style={{
            padding: "16px 24px", background: C.card,
            borderBottom: `1px solid ${C.border}`,
            display: "flex", alignItems: "center", gap: 12,
        }}>
        <LED color={C.dim} size={8} />
        <span style={{ color: C.dim, fontSize: 12, letterSpacing: "0.06em" }}>
        FORGE IDLE — select a file from the candidates list to add an AC3 5.1 track
        </span>
        </div>
    );

    const f   = job.file || {};
    const pct = job.progress || 0;

    return (
        <div style={{
            padding: "14px 24px", background: C.card,
            borderBottom: `1px solid ${C.border}`,
            borderLeft: `3px solid ${C.blue}`,
        }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <LED color={C.blue} pulse size={8} />
        <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
        {job.is_undo ? "UNDOING" : "FORGING"}
        </span>
        <span style={{ marginLeft: "auto", color: C.muted, fontSize: 11 }}>
        {job.current_action || (job.is_undo ? "Removing AC3 5.1 track" : "Adding AC3 5.1 track")}
        </span>
        </div>

        <div style={{
            color: C.text, fontSize: 14, fontWeight: 600, marginBottom: 4,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
        {f.filename || "Unknown file"}
        </div>
        <div style={{
            color: C.dim, fontSize: 11, marginBottom: 12,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
        {f.path || ""}
        </div>

        <SegBar value={pct} />

        <div style={{ display: "flex", gap: 28, marginTop: 10 }}>
        <Stat label="PROGRESS" value={`${pct.toFixed(1)}%`} color={C.blue} />
        <Stat label="SIZE"     value={fmtSize(f.size)} />
        <Stat label="DURATION" value={fmtDur(f.duration)} />
        <Stat label="ACTION"   value={job.is_undo ? "Removing AC3" : "Adding AC3"} color={job.is_undo ? C.red : C.amber} />
        </div>
        </div>
    );
};
