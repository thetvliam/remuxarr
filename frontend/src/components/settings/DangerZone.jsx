import { useState, useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { CONFIRM_MS } from "../../constants";

/* ── Danger Zone — Clear Database ────────────────────────────────────────────
 * Wipes all scanned-file/track/queue/history/forge data so the next scan
 * behaves like a first-run baseline scan. App settings are NOT touched —
 * the backend endpoint only deletes from the scan-state tables.
 * Requires a second click within 4 seconds to confirm.
 *
 * onCleared fires after a successful wipe. The endpoint broadcasts nothing,
 * and this component is reached from Settings while the dashboard panels are
 * unmounted, so without it the queue kept listing rows the wipe had already
 * deleted — clicking one opened a detail fetch that 404'd, and dismissing one
 * addressed a dead id. ─────────────────────────────────────────────────────*/
export const DangerZone = ({ api, toast, onCleared }) => {
    const { palette, type, space, radius } = useTheme();
    const [confirming, setConfirming] = useState(false);
    const [clearing,   setClearing]   = useState(false);

    // Auto-cancel the confirmation state after 4 seconds of inactivity.
    // The cleanup (clearTimeout) is CRITICAL: prevents multiple stacked
    // timeouts from queuing up and resetting `confirming` unexpectedly if
    // the user clicks rapidly. Do not remove or restructure this effect.
    useEffect(() => {
        if (!confirming) return;
        const t = setTimeout(() => setConfirming(false), CONFIRM_MS);
        return () => clearTimeout(t);
    }, [confirming]);

    const handleClick = async () => {
        if (!confirming) {
            setConfirming(true);
            return;
        }

        setClearing(true);
        try {
            const r = await fetch(`${api}/api/settings/clear-database`, { method: "POST" });
            if (r.ok) {
                // Before the toast, not after: a throw in the toast call must
                // not be what stops the panels being told the data is gone.
                onCleared?.();
                toast?.("Database cleared — next scan will treat all files as new", "success");
            } else {
                toast?.("Failed to clear database", "error");
            }
        } catch (err) {
            console.error("Clear database failed", err);
            toast?.("Failed to clear database", "error");
        } finally {
            setClearing(false);
            setConfirming(false);
        }
    };

    return (
        <div style={{ marginTop: space.giant, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
        <div style={{ color: palette.red, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold, marginBottom: space.xl }}>
        DANGER ZONE
        </div>

        <div style={{
            display: "flex",
            alignItems: "flex-start",
            gap: space.huge,
            padding: `${space.xl}px 0`,
        }}>
        <div style={{ flex: 1 }}>
        <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xs }}>
        Clear Database
        </div>
        <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
        Wipes all scanned files, tracks, queue items, history, and forge jobs.
        Your settings — media library paths, language preferences, dry-run mode,
        etc. — are preserved. The next scan will treat every file as new,
        exactly like the first run.
        </div>
        </div>

        <button
        onClick={handleClick}
        disabled={clearing}
        style={{
            padding: `${space.xs}px ${space.xl}px`,
            background: confirming ? alpha(palette.red, ALPHA.medium) : "transparent",
            border: `1px solid ${palette.red}`,
            borderRadius: radius.sm,
            color: palette.red,
            fontSize: type.size.sm,
            fontFamily: type.family,
            fontWeight: type.weight.bold,
            letterSpacing: type.tracking.wide,
            cursor: clearing ? "not-allowed" : "pointer",
            whiteSpace: "nowrap",
            flexShrink: 0,
        }}
        >
        {clearing ? "CLEARING…" : confirming ? "CLICK AGAIN TO CONFIRM" : "CLEAR DATABASE"}
        </button>
        </div>
        </div>
    );
};
