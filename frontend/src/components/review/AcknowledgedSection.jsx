import { useState, useEffect, useCallback } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";

/* ═══════════════════════════════════════════════════════════════════════════
 * ACKNOWLEDGED THRESHOLDS SECTION
 *
 * Files exempted from the undefined-audio threshold by a past Approve.
 *
 * Approve sets und_audio_threshold_acknowledged and, until the endpoints
 * behind this section existed, nothing ever set it back: one write site,
 * True, and no route to False. The file was exempt for good and there was
 * nowhere to see that it was.
 *
 * That matters more than a stray flag because the Approve button used to say
 * it would "process the file now, keeping every audio track". It does not,
 * whenever nothing else needs doing — the file is marked Skipped instead. So
 * an unknown number of these exemptions were given on a false description,
 * and this list is how someone finds them.
 *
 * Clearing is per-file with multi-select, deliberately, rather than one
 * button that empties the list. The whole point of the section is that an
 * action people could not see or undo is bad; replacing it with a single
 * irreversible-feeling sweep would repeat the mistake in a new place.
 *
 * The file comes back on the NEXT SCAN, not immediately — the endpoint
 * invalidates the scan stamp rather than reprocessing on the spot. Same as
 * Skip, whose wording this borrows.
 ═══════════════════════════════════════════════════════════════════════════ */
export const AcknowledgedSection = ({ api, toast, refreshKey }) => {
    const { palette, type, space, radius } = useTheme();
    const accent = palette.amber;

    const [files, setFiles] = useState([]);
    const [total, setTotal] = useState(0);
    const [selected, setSelected] = useState(() => new Set());
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const r = await fetch(`${api}/api/queue/acknowledged`);
            if (!r.ok) throw new Error(String(r.status));
            const data = await r.json();
            setFiles(data.files || []);
            setTotal(data.total || 0);
            /* Selection is dropped on every reload rather than reconciled
             * against the new list. A stale id would silently do nothing on
             * clear, and a checkbox that looks ticked but clears nothing is
             * worse than making the user pick again. */
            setSelected(new Set());
        } catch {
            setFiles([]);
            setTotal(0);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => { load(); }, [load, refreshKey]);

    const toggle = (id) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const clearSelected = async () => {
        setBusy(true);
        try {
            const r = await fetch(`${api}/api/queue/acknowledged/clear`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ file_ids: [...selected] }),
            });
            if (!r.ok) {
                toast?.("Could not clear the acknowledgement", "error");
                return;
            }
            const data = await r.json();
            /* "cleared" counts files that actually had the flag, which can be
             * fewer than were selected if the list moved. Reporting the
             * request size instead would overstate the work. */
            const n = data.cleared ?? 0;
            toast?.(
                `${n} file${n === 1 ? "" : "s"} will be reviewed again on the next scan`,
                "info",
            );
            await load();
        } catch {
            toast?.("Could not clear the acknowledgement", "error");
        } finally {
            setBusy(false);
        }
    };

    /* Hidden entirely when empty. Every other section on this page keeps its
     * heading and shows an empty state, because those lists are part of the
     * routine. This one describes a problem most libraries do not have, and a
     * permanent "no exempted files" heading would be clutter on the page it
     * is meant to keep readable. */
    if (!loading && total === 0) return null;

    return (
        <div>
        <div style={{
            display: "flex",
            alignItems: "center",
            gap: space.md,
            marginBottom: space.sm,
            paddingTop: space.huge,
            borderTop: `1px solid ${palette.border}`,
        }}>
        <span style={{ color: accent, fontSize: type.size.xxl }}>⤺</span>
        <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
        APPROVED UNDEFINED-AUDIO THRESHOLDS
        </span>
        <span style={{
            padding: `0 ${space.xs}px`,
            background: alpha(accent, ALPHA.mild),
            border: `1px solid ${alpha(accent, ALPHA.strong)}`,
            borderRadius: radius.sm,
            color: accent,
            fontSize: type.size.xs,
        }}>
        {total}
        </span>
        </div>

        <p style={{ color: palette.muted, fontSize: type.size.md, margin: `0 0 ${space.xl}px`, lineHeight: type.leading.relaxed }}>
        Files you approved past the undefined audio track threshold. They are
        exempt from that check for good and will not appear in the review list
        above again. Clearing an approval puts the file back under the
        threshold — it returns on the next scan, and nothing about the file
        itself is changed either way.
        </p>

        <div style={{ display: "flex", gap: space.sm, marginBottom: space.md }}>
        <Btn
        label={busy ? "WORKING…" : `CLEAR APPROVAL (${selected.size} ${selected.size === 1 ? "file" : "files"})`}
        color={accent}
        bg={alpha(accent, ALPHA.low)}
        onClick={clearSelected}
        disabled={busy || selected.size === 0}
        />
        </div>

        {loading ? (
            <EmptyState msg="Loading…" />
        ) : (
            <>
            {files.map(f => (
                <label
                key={f.id}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: space.md,
                    padding: `${space.sm}px ${space.md}px`,
                    borderBottom: `1px solid ${palette.border}`,
                    cursor: "pointer",
                }}
                >
                <input
                type="checkbox"
                checked={selected.has(f.id)}
                onChange={() => toggle(f.id)}
                />
                <span style={{ color: palette.text, fontSize: type.size.md }}>
                {f.filename}
                </span>
                <span style={{ color: palette.dim, fontSize: type.size.xs, marginLeft: "auto" }}>
                {f.status}
                </span>
                </label>
            ))}
            {/* The endpoint is paginated and this fetches one page. Saying so
              * beats a list that silently stops: the count in the heading is
              * the real total, so without this line the two disagree and the
              * page looks wrong rather than truncated. */}
            {total > files.length && (
                <p style={{ color: palette.dim, fontSize: type.size.xs, marginTop: space.sm }}>
                Showing {files.length} of {total}. Clear some to see the rest.
                </p>
            )}
            </>
        )}
        </div>
    );
};
