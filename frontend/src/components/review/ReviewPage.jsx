import { C } from "../../constants";
import { fmtSize, fmtDur } from "../../utils";
import { Stat } from "../atoms/Stat";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { AudioLanguageReviewSection } from "./AudioLanguageReviewSection";

/* ═══════════════════════════════════════════════════════════════════════════
 * MANUAL REVIEW PAGE
 * Lists files that triggered the "multiple undefined audio tracks" gate.
 * User can approve (send to queue) or skip (dismiss).
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const ReviewPage = ({ api, items, onRefresh }) => {
    const approve = async (id) => {
        await fetch(`${api}/api/queue/${id}/approve`, { method: "POST" }).catch(() => {});
        onRefresh();
    };
    const skip = async (id) => {
        await fetch(`${api}/api/queue/${id}`, { method: "DELETE" }).catch(() => {});
        onRefresh();
    };
    const resolveSubtitle = async (id, streamIndex, choice) => {
        await fetch(`${api}/api/queue/${id}/resolve-subtitles`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ overrides: { [streamIndex]: choice } }),
        }).catch(() => {});
        onRefresh();
    };

    return (
        <div style={{ maxWidth: 860, margin: "0 auto", padding: "28px 22px" }}>
        {/* Page header */}
        <div style={{ marginBottom: 24 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span style={{ color: C.yellow, fontSize: 15 }}>⚠</span>
        <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
        MANUAL REVIEW
        </span>
        <span style={{
            padding: "0 6px",
            background: C.yellow + "20",
            border: `1px solid ${C.yellow}44`,
            color: C.yellow,
            fontSize: 9,
        }}>
        {items.length}
        </span>
        </div>
        <p style={{ color: C.muted, fontSize: 11, margin: 0, lineHeight: 1.65 }}>
        Files end up here for two reasons: two or more audio tracks with an
        undefined language (approve to process anyway, or skip to dismiss),
            or subtitle tracks that can't be converted to external SRT — choose
            KEEP or REMOVE for each flagged track below.
            </p>
            </div>

            {items.length === 0
                ? <EmptyState msg="No files pending manual review — all clear ✓" />
                : items.map(item => {
                    const f = item.file || {};
                    const flagged = item.flagged_subtitles;

                    return (
                        <div
                        key={item.id}
                        style={{
                            padding: "14px 16px",
                            background: C.card,
                            border: `1px solid #3a2800`,
                            borderLeft: `3px solid ${C.yellow}`,
                            marginBottom: 10,
                        }}
                        >
                        <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                            color: C.text,
                            fontSize: 13,
                            fontWeight: 600,
                            marginBottom: 3,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}>
                        {f.filename || "—"}
                        </div>
                        <div style={{
                            color: C.dim,
                            fontSize: 10,
                            marginBottom: 7,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}>
                        {f.path}
                        </div>
                        <div style={{ color: C.yellow, fontSize: 11, lineHeight: 1.55 }}>
                        {item.reason}
                        </div>
                        <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
                        <Stat label="SIZE"     value={fmtSize(f.size)} />
                        <Stat label="DURATION" value={fmtDur(f.duration)} />
                        </div>
                        </div>

                        {/* Audio-type review: simple Approve / Skip */}
                        {!flagged && (
                            <div style={{ display: "flex", gap: 8, flexShrink: 0, paddingTop: 2 }}>
                            <Btn label="APPROVE" color={C.green} bg={C.green + "18"} onClick={() => approve(item.id)} />
                            <Btn label="SKIP"    color={C.red}   bg={C.red   + "18"} onClick={() => skip(item.id)} />
                            </div>
                        )}
                        </div>

                        {/* Subtitle-type review: per-track Keep/Remove */}
                        {flagged && flagged.length > 0 && (
                            <div style={{ marginTop: 12, borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
                            {flagged.map(track => (
                                <div
                                key={track.stream_index}
                                style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 12,
                                    padding: "8px 10px",
                                    background: "#00000022",
                                    border: `1px solid ${C.border}`,
                                    marginBottom: 6,
                                }}
                                >
                                <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ color: C.text, fontSize: 11, fontWeight: 600, marginBottom: 2 }}>
                                {track.title || `Stream ${track.stream_index}`}
                                </div>
                                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                                <span style={{
                                    padding: "1px 6px",
                                    background: C.yellow + "18",
                                    border: `1px solid ${C.yellow}44`,
                                    color: C.yellow,
                                    fontSize: 9,
                                    letterSpacing: "0.1em",
                                }}>
                                {(track.language || "und").toUpperCase()} · {track.codec}
                                {track.is_forced ? " · FORCED" : ""}
                                </span>
                                <span style={{ color: C.dim, fontSize: 10 }}>stream {track.stream_index}</span>
                                </div>
                                </div>
                                <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                                <Btn
                                label="KEEP"
                                color={C.green}
                                bg={C.green + "18"}
                                onClick={() => resolveSubtitle(item.id, track.stream_index, "keep")}
                                />
                                <Btn
                                label="REMOVE"
                                color={C.red}
                                bg={C.red + "18"}
                                onClick={() => resolveSubtitle(item.id, track.stream_index, "remove")}
                                />
                                </div>
                                </div>
                            ))}
                            </div>
                        )}
                        </div>
                    );
                })
            }

            <AudioLanguageReviewSection api={api} />
            </div>
    );
};
