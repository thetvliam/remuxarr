import { useState } from "react";
import { useTheme } from "../../theme";
import { ForgeActivePanel } from "./ForgeActivePanel";
import { CandidatesPanel } from "./CandidatesPanel";
import { ForgeProcessedPanel } from "./ForgeProcessedPanel";

// ── Root ForgePage ─────────────────────────────────────────────────────────

export const ForgePage = ({
  api, forgeRefreshKey,
  active, processed,
  onAdd, onUndo,
  workerPaused = false,
  isMobile = false,
}) => {
  const { palette, type, space, size } = useTheme();
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
      <div style={{
        display: "flex",
        flexShrink: 0,
        borderBottom: `1px solid ${palette.border}`,
        background: palette.card,
      }}>
      {[["candidates", "CANDIDATES"], ["processed", "PROCESSED"]].map(([k, l]) => (
        <button
        key={k}
        onClick={() => setForgeTab(k)}
        style={{
          flex: 1,
          padding: `${space.md}px 0`,
          background: "transparent",
          border: "none",
          borderBottom: forgeTab === k
          ? `${size.accentThin}px solid ${palette.amber}` : `${size.accentThin}px solid transparent`,
          color: forgeTab === k ? palette.amber : palette.dim,
          fontSize: type.size.xs,
          fontFamily: type.family,
          letterSpacing: type.tracking.widest,
          fontWeight: type.weight.bold,
          cursor: "pointer",
        }}
        >
        {l}
        </button>
      ))}
      </div>
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
