import { useState, useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";

/* ── Danger Zone — Clear Database ────────────────────────────────────────────
 * Wipes all scanned-file/track/queue/history/forge data so the next scan
 * behaves like a first-run baseline scan. App settings are NOT touched —
 * the backend endpoint only deletes from the scan-state tables.
 * Requires a second click within 4 seconds to confirm. ──────────────────── */
export const DangerZone = ({ api, toast }) => {
    const { palette, type, space, legacy } = useTheme();
    const [confirming, setConfirming] = useState(false);
    const [clearing,   setClearing]   = useState(false);

    // Auto-cancel the confirmation state after 4 seconds of inactivity.
    // The cleanup (clearTimeout) is CRITICAL: prevents multiple stacked
    // timeouts from queuing up and resetting `confirming` unexpectedly if
    // the user clicks rapidly. Do not remove or restructure this effect.
    useEffect(() => {
        if (!confirming) return;
        const t = setTimeout(() => setConfirming(false), 4000);
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
                toast?.("Database cleared — next scan will treat all files as new", palette.green);
            } else {
                toast?.("Failed to clear database", palette.red);
            }
        } catch (_) {
            toast?.("Failed to clear database", palette.red);
        } finally {
            setClearing(false);
            setConfirming(false);
        }
    };

    return (
        <div style={{ marginTop: legacy.sectionSepGapY, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
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
        <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: legacy.labelGapY }}>
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
            padding: `${space.xs}px ${legacy.actionPadX}px`,
            background: confirming ? alpha(palette.red, ALPHA.medium) : "transparent",
            border: `1px solid ${palette.red}`,
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
