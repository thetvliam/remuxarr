import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { useArmedConfirm } from "../../hooks/useArmedConfirm";

/* ── Danger Zone — Clear Database ────────────────────────────────────────────
 * Wipes all scanned-file/track/queue/history/forge data so the next scan
 * behaves like a first-run baseline scan, and empties the recycle bin —
 * revert points and their sidecars go too, because file ids are reused
 * after a wipe and a kept point would end up on an unrelated file (see
 * settings.clear_database). The description says so: someone clearing the
 * database for a fresh scan should not lose every undo without being told.
 * App settings are NOT touched — the backend endpoint only deletes from
 * the scan-state tables.
 * Requires a second click within 4 seconds to confirm.
 *
 * onCleared fires after a successful wipe. The endpoint broadcasts nothing,
 * and this component is reached from Settings while the dashboard panels are
 * unmounted, so without it the queue kept listing rows the wipe had already
 * deleted — clicking one opened a detail fetch that 404'd, and dismissing one
 * addressed a dead id. ─────────────────────────────────────────────────────*/
export const DangerZone = ({ api, toast, onCleared }) => {
    const { palette, type, space, radius } = useTheme();
    // Two clicks, disarming itself after CONFIRM_MS: see useArmedConfirm.
    const { armed: confirming, arm, disarm } = useArmedConfirm();
    const [clearing,   setClearing]   = useState(false);

    const handleClick = async () => {
        if (!confirming) {
            arm();
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
            disarm();
        }
    };

    return (
        <div style={{ marginTop: space.giant, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
        <div style={{ color: palette.red, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold, marginBottom: space.xl }}>
        DANGER ZONE
        </div>

        {/* May wrap: the description keeps at least 220px and the button
          * drops below it on a narrow phone, as in MaintenanceSection. */}
        <div style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "flex-start",
            gap: space.huge,
            padding: `${space.xl}px 0`,
        }}>
        <div style={{ flex: "1 1 220px" }}>
        <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xs }}>
        Clear Database
        </div>
        <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
        Wipes all scanned files, tracks, queue items, history and forge jobs,
        and empties the recycle bin: every stored revert point is deleted and
        can&apos;t be restored. Your settings — media library paths, language preferences, dry-run mode,
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
