import { useState, useEffect } from "react";
import { C } from "../../constants";

/* ── Danger Zone — Clear Database ────────────────────────────────────────────
 * Wipes all scanned-file/track/queue/history/forge data so the next scan
 * behaves like a first-run baseline scan. App settings are NOT touched —
 * the backend endpoint only deletes from the scan-state tables.
 * Requires a second click within 4 seconds to confirm. ──────────────────── */
export const DangerZone = ({ api, toast }) => {
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
                toast?.("Database cleared — next scan will treat all files as new", C.green);
            } else {
                toast?.("Failed to clear database", C.red);
            }
        } catch (_) {
            toast?.("Failed to clear database", C.red);
        } finally {
            setClearing(false);
            setConfirming(false);
        }
    };

    return (
        <div style={{ marginTop: 36, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
        <div style={{ color: C.red, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700, marginBottom: 16 }}>
        DANGER ZONE
        </div>

        <div style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 24,
            padding: "16px 0",
        }}>
        <div style={{ flex: 1 }}>
        <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 5 }}>
        Clear Database
        </div>
        <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65 }}>
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
            padding: "6px 14px",
            background: confirming ? C.red + "22" : "transparent",
            border: `1px solid ${C.red}`,
            color: C.red,
            fontSize: 10,
            fontFamily: "inherit",
            fontWeight: 700,
            letterSpacing: "0.1em",
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
