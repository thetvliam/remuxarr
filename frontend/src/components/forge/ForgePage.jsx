import { useState } from "react";
import { C } from "../../constants";
import { ForgeActivePanel } from "./ForgeActivePanel";
import { CandidatesPanel } from "./CandidatesPanel";
import { ForgeProcessedPanel } from "./ForgeProcessedPanel";

// ── Root ForgePage ─────────────────────────────────────────────────────────

export const ForgePage = ({
  api, forgeRefreshKey,
  active, processed,
  onAdd, onUndo,
  isMobile = false,
}) => {
  const [forgeTab, setForgeTab] = useState("candidates"); // mobile only

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
    <ForgeActivePanel job={active} />

    <div style={{
      flex: 1,
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
      borderTop: `1px solid ${C.border}`,
    }}>
    {/* Mobile tab bar */}
    {isMobile && (
      <div style={{
        display: "flex",
        flexShrink: 0,
        borderBottom: `1px solid ${C.border}`,
        background: C.card,
      }}>
      {[["candidates", "CANDIDATES"], ["processed", "PROCESSED"]].map(([k, l]) => (
        <button
        key={k}
        onClick={() => setForgeTab(k)}
        style={{
          flex: 1,
          padding: "10px 0",
          background: "transparent",
          border: "none",
          borderBottom: forgeTab === k
          ? `2px solid ${C.amber}` : "2px solid transparent",
          color: forgeTab === k ? C.amber : C.dim,
          fontSize: 9,
          fontFamily: "inherit",
          letterSpacing: "0.14em",
          fontWeight: 700,
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
        borderRight: !isMobile ? `1px solid ${C.border}` : "none",
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
