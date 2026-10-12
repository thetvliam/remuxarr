import { useState } from "react";
import { useTheme } from "../../theme";
import { ForgeActivePanel } from "./ForgeActivePanel";
import { CandidatesPanel } from "./CandidatesPanel";
import { ForgeProcessedPanel } from "./ForgeProcessedPanel";
import { TabBar } from "../atoms/TabBar";

// ── Root ForgePage ─────────────────────────────────────────────────────────

export const ForgePage = ({
  api, forgeRefreshKey,
  active, processed,
  onAdd, onUndo,
  workerPaused = false,
  isMobile = false,
}) => {
  const { palette } = useTheme();
  const [forgeTab, setForgeTab] = useState("candidates"); // mobile only

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
    <ForgeActivePanel job={active} workerPaused={workerPaused} />

    <div style={{
      flex: 1,
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
      borderTop: `1px solid ${palette.border}`,
    }}>
    {/* Mobile tab bar */}
    {isMobile && (
      <TabBar
      label="Forge panels"
      value={forgeTab}
      onChange={setForgeTab}
      tabs={[{ value: "candidates", label: "CANDIDATES" }, { value: "processed", label: "PROCESSED" }]}
      />
    )}

    {/* Panel area */}
    <div style={{
      flex: 1,
      display: "flex",
      overflow: "hidden",
    }}>
    {(!isMobile || forgeTab === "candidates") && (
      <div style={{
        flex: 1,
        borderRight: !isMobile ? `1px solid ${palette.border}` : "none",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}>
      <CandidatesPanel
      api={api}
      forgeRefreshKey={forgeRefreshKey}
      onAdd={onAdd}
      />
      </div>
    )}

    {(!isMobile || forgeTab === "processed") && (
      <div style={{
        flex: 1,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}>
      <ForgeProcessedPanel jobs={processed} onUndo={onUndo} />
      </div>
    )}
    </div>
    </div>
    </div>
  );
};
