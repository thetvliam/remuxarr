import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { useArmedConfirm } from "../../hooks/useArmedConfirm";
import { Btn } from "../atoms/Btn";
import { SectionHeader } from "./SectionHeader";
import { ActionRow } from "./ActionRow";

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
export const DangerZone = ({ api, toast, onCleared, first = false }) => {
    const { palette } = useTheme();
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
        <div>
        <SectionHeader label="Danger Zone" first={first} tone={palette.red} />

        <ActionRow
        title="Clear Database"
        description={<>
          Wipes all scanned files, tracks, queue items, history and forge jobs,
          and empties the recycle bin: every stored revert point is deleted and
          can&apos;t be restored. Your settings — media library paths, language preferences, dry-run mode,
          etc. — are preserved. The next scan will treat every file as new,
          exactly like the first run.
        </>}
        action={
          <Btn
          size="md"
          label={clearing ? "CLEARING…" : confirming ? "CLICK AGAIN TO CONFIRM" : "CLEAR DATABASE"}
          color={palette.red}
          bg={confirming ? alpha(palette.red, ALPHA.medium) : undefined}
          onClick={handleClick}
          disabled={clearing}
          />
        }
        />
        </div>
    );
};
