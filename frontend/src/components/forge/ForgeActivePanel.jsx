import { useTheme } from "../../theme";
import { fmtSize, fmtDur } from "../../utils";
import { LED } from "../atoms/LED";
import { Stat } from "../atoms/Stat";
import { SegBar } from "../bars/SegBar";

export const ForgeActivePanel = ({ job }) => {
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

    return (
        <div style={{
            padding: `${space.xl}px ${space.huge}px`, background: palette.card,
            borderBottom: `1px solid ${palette.border}`,
            borderLeft: `${size.accentWidth}px solid ${palette.blue}`,
        }}>
        <div style={{ display: "flex", alignItems: "center", gap: space.md, marginBottom: space.md }}>
        <LED color={palette.blue} pulse size={size.ledSizeLg} />
        <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
        {job.is_undo ? "UNDOING" : "FORGING"}
        </span>
        <span style={{ marginLeft: "auto", color: palette.muted, fontSize: type.size.md }}>
        {job.current_action || (job.is_undo ? "Removing AC3 5.1 track" : "Adding AC3 5.1 track")}
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

        <SegBar value={pct} />

        <div style={{ display: "flex", gap: space.max, marginTop: space.md }}>
        <Stat label="PROGRESS" value={`${pct.toFixed(1)}%`} color={palette.blue} />
        <Stat label="SIZE"     value={fmtSize(f.size)} />
        <Stat label="DURATION" value={fmtDur(f.duration)} />
        <Stat label="ACTION"   value={job.is_undo ? "Removing AC3" : "Adding AC3"} color={job.is_undo ? palette.red : palette.amber} />
        </div>
        </div>
    );
};
