import { useEffect } from "react";
import { C, STATUS_COLOR, ACTION_CFG } from "../constants";
import { fmtSize, fmtDur, formatBytesSaved } from "../utils";
import { StatusBadge } from "./atoms/StatusBadge";
import { ActionBadge } from "./atoms/ActionBadge";
import { Stat } from "./atoms/Stat";
import { Btn } from "./atoms/Btn";

/* ═══════════════════════════════════════════════════════════════════════════
 * DETAIL MODAL
 * Opens when clicking any item in the Queue or History panels.
 * Shows: file metadata · reason for queuing · full planned-actions list.
 * Escape key closes it.
 *
 * NOTE: deliberately NOT wrapped in React.memo. The parent does two-phase
 * loading — opens with basic item data immediately, then enriches it with
 * a second fetch that includes planned_actions. An unoptimised component
 * re-renders correctly on every prop change; memoising this with default
 * shallow comparison risks the enriched data silently failing to render.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const DetailModal = ({ item, onClose, onRetry, retryLabel = "RETRY", onDismiss, isMobile = false }) => {
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!item) return null;

  const f       = item.file || {};
  // planned_actions is undefined until the detail fetch returns (the list
  // endpoint omits it to avoid loading actions for every queued item on
  // every poll). null/undefined → still loading; [] → fetched but empty.
  const actions  = item.planned_actions;
  const loading  = actions === undefined || actions === null;
  const topColor = (actions?.length > 0)
  ? (ACTION_CFG[actions[0].action_type]?.text || C.amber)
  : C.amber;
  const bs = formatBytesSaved(item.bytes_saved, item.bytes_saved_pct);

  return (
    <div
    onClick={onClose}
    style={{
      position: "fixed",
      inset: 0,
      // Desktop: dimmed backdrop centred over content.
      // Mobile: solid background — the sheet fills the full screen so
      // there's nothing to blur behind it.
      background: isMobile ? C.card : "#000000bb",
      display: "flex",
      alignItems: isMobile ? "flex-start" : "center",
      justifyContent: "center",
      zIndex: 1000,
      backdropFilter: isMobile ? "none" : "blur(3px)",
    }}
    >
    <div
    onClick={e => e.stopPropagation()}
    style={{
      background: C.card,
      border: `1px solid ${C.border}`,
      borderTop: `2px solid ${topColor}`,
      // Desktop: centred card with max dimensions.
      // Mobile: full-screen — use 100dvh so the browser address bar
      // doesn't cause overflow (dvh accounts for the visible viewport
      // height, unlike vh which can be obscured by the address bar).
      ...(isMobile
      ? {
        width: "100%",
        height: "100dvh",
        maxHeight: "100dvh",
        maxWidth: "none",
        borderRadius: 0,
        display: "flex",
        flexDirection: "column",
      }
      : {
        width: "90%",
        maxWidth: 560,
        maxHeight: "82vh",
        display: "flex",
        flexDirection: "column",
        animation: "modalIn 0.15s ease",
      }
      ),
    }}
    >
    {/* Header */}
    <div style={{
      padding: "12px 18px",
      borderBottom: `1px solid ${C.border}`,
      display: "flex",
      alignItems: "center",
      gap: 10,
    }}>
    <StatusBadge status={item.status} />
    <span style={{
      flex: 1,
      color: C.text,
      fontSize: 13,
      fontWeight: 600,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    }}>
    {f.filename || "—"}
    </span>
    {/* × always visible; on mobile it's the primary close affordance
      since there's no backdrop to tap. */}
      <button
      onClick={onClose}
      style={{
        background: "none",
        border: "none",
        color: C.muted,
        fontSize: isMobile ? 24 : 20,
        cursor: "pointer",
        lineHeight: 1,
        padding: isMobile ? "0 4px" : "0 2px",
        fontFamily: "inherit",
      }}
      >
      ×
      </button>
      </div>

      {/* File meta row */}
      <div style={{
        padding: "12px 18px",
        borderBottom: `1px solid ${C.border}`,
        display: "flex",
        gap: 24,
        flexWrap: "wrap",
      }}>
      <Stat label="SIZE"      value={fmtSize(f.size)} />
      <Stat label="DURATION"  value={fmtDur(f.duration)} />
      <Stat label="CONTAINER" value={(f.container || "").toUpperCase() || "—"} />
      {bs?.isPositive && (
        <Stat
        label="SAVED"
        value={`−${bs.sizeText} (${bs.pctDisplay}%)`}
        color={C.green}
        />
      )}
      {bs?.isNegative && (
        <Stat
        label="OVERHEAD"
        value={`+${bs.sizeText}`}
        color={C.dim}
        />
      )}
      </div>

      {/* Reason */}
      <div style={{ padding: "10px 18px", borderBottom: `1px solid ${C.border}` }}>
      <div style={{ color: C.dim, fontSize: 9, letterSpacing: "0.12em", marginBottom: 5 }}>
      REASON
      </div>
      <div style={{ color: C.text, fontSize: 11, lineHeight: 1.65 }}>
      {item.reason || "No reason recorded"}
      </div>
      </div>

      {/* Planned actions — the key "why" breakdown */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 18px" }}>
      <div style={{ color: C.dim, fontSize: 9, letterSpacing: "0.12em", marginBottom: 10 }}>
      PLANNED ACTIONS
      </div>
      {loading ? (
        <span style={{ color: C.dim, fontSize: 11 }}>Loading…</span>
      ) : actions.length === 0 ? (
        <span style={{ color: C.muted, fontSize: 11 }}>No actions recorded</span>
      ) : (
        actions.map((a, i) => {
          const cfg = ACTION_CFG[a.action_type] || { bg: "#111", border: C.border };
          return (
            <div
            key={i}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
              marginBottom: 7,
              padding: "8px 10px",
              background: cfg.bg,
              border: `1px solid ${cfg.border}`,
            }}
            >
            <ActionBadge type={a.action_type} />
            <span style={{ color: C.text, fontSize: 11, lineHeight: 1.6 }}>
            {a.description}
            </span>
            </div>
          );
        })
      )}
      </div>

      {/* Error (failed items only) */}
      {item.error_message && (
        <div style={{
          padding: "10px 18px",
          borderTop: `1px solid ${C.border}`,
          background: "#180a0a",
        }}>
        <div style={{ color: C.dim, fontSize: 9, letterSpacing: "0.12em", marginBottom: 5 }}>
        ERROR
        </div>
        <div style={{
          color: C.red,
          fontSize: 10,
          lineHeight: 1.55,
          maxHeight: 90,
          overflowY: "auto",
          whiteSpace: "pre-wrap",
        }}>
        {item.error_message}
        </div>
        </div>
      )}

      {/* Full path footer */}
      <div style={{
        padding: "7px 18px",
        borderTop: `1px solid ${C.border}`,
        overflow: "hidden",
      }}>
      <div style={{
        color: C.dim,
        fontSize: 10,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}>
      {f.path || "—"}
      </div>
      </div>

      {/* Action buttons — only shown for terminal states */}
      {(onRetry || onDismiss) && (
        <div style={{
          padding: "10px 18px",
          borderTop: `1px solid ${C.border}`,
          display: "flex",
          gap: 8,
          justifyContent: "flex-end",
          background: C.card,
        }}>
        {onDismiss && (
          <Btn label="DISMISS" color={C.muted} onClick={onDismiss} />
        )}
        {onRetry && (
          item.status === "dry_run" ? (
            <Btn label="▶ PROCESS NOW" color={C.green} bg={C.green + "18"} onClick={onRetry} />
          ) : (
            <Btn label={`↻ ${retryLabel}`} color={C.amber} bg={C.amber + "18"} onClick={onRetry} />
          )
        )}
        </div>
      )}
      </div>
      </div>
  );
};
