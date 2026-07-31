import { useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../theme";
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
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const DetailModal = ({ item, onClose, onRetry, retryLabel = "RETRY", onDismiss, isMobile = false }) => {
  const { palette, type, space, radius, legacy, actionCfg } = useTheme();
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
  ? (actionCfg[actions[0].action_type]?.text || palette.amber)
  : palette.amber;
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
      background: isMobile ? palette.card : legacy.modalScrimBg,
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
      background: palette.card,
      border: `1px solid ${palette.border}`,
      borderTop: `${legacy.accentThin}px solid ${topColor}`,
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
        borderRadius: radius.none,
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
      padding: `${space.lg}px ${space.xxl}px`,
      borderBottom: `1px solid ${palette.border}`,
      display: "flex",
      alignItems: "center",
      gap: space.md,
    }}>
    <StatusBadge status={item.status} />
    <span style={{
      flex: 1,
      color: palette.text,
      fontSize: type.size.lg,
      fontWeight: type.weight.semibold,
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
        color: palette.muted,
        fontSize: isMobile ? legacy.closeGlyphMobile : legacy.closeGlyph,
        cursor: "pointer",
        lineHeight: type.leading.none,
        padding: isMobile ? `0 ${space.xxs}px` : `0 ${space.hair}px`,
        fontFamily: type.family,
      }}
      >
      ×
      </button>
      </div>

      {/* File meta row */}
      <div style={{
        padding: `${space.lg}px ${space.xxl}px`,
        borderBottom: `1px solid ${palette.border}`,
        display: "flex",
        gap: space.huge,
        flexWrap: "wrap",
      }}>
      <Stat label="SIZE"      value={fmtSize(f.size)} />
      <Stat label="DURATION"  value={fmtDur(f.duration)} />
      <Stat label="CONTAINER" value={(f.container || "").toUpperCase() || "—"} />
      {bs?.isPositive && (
        <Stat
        label="SAVED"
        value={`−${bs.sizeText} (${bs.pctDisplay}%)`}
        color={palette.green}
        />
      )}
      {bs?.isNegative && (
        <Stat
        label="OVERHEAD"
        value={`+${bs.sizeText}`}
        color={palette.dim}
        />
      )}
      </div>

      {/* Reason */}
      <div style={{ padding: `${space.md}px ${space.xxl}px`, borderBottom: `1px solid ${palette.border}` }}>
      <div style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.wider, marginBottom: space.xs }}>
      REASON
      </div>
      <div style={{ color: palette.text, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
      {item.reason || "No reason recorded"}
      </div>
      </div>

      {/* Planned actions — the key "why" breakdown */}
      <div style={{ flex: 1, overflowY: "auto", padding: `${space.lg}px ${space.xxl}px` }}>
      <div style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.wider, marginBottom: space.md }}>
      PLANNED ACTIONS
      </div>
      {loading ? (
        <span style={{ color: palette.dim, fontSize: type.size.md }}>Loading…</span>
      ) : actions.length === 0 ? (
        <span style={{ color: palette.muted, fontSize: type.size.md }}>No actions recorded</span>
      ) : (
        actions.map((a, i) => {
          const cfg = actionCfg[a.action_type] || { bg: legacy.badgeFallbackBg, border: palette.border };
          return (
            <div
            key={i}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: space.md,
              marginBottom: space.sm,
              padding: `${space.sm}px ${space.md}px`,
              background: cfg.bg,
              border: `1px solid ${cfg.border}`,
              borderRadius: radius.sm,
            }}
            >
            <ActionBadge type={a.action_type} />
            <span style={{ color: palette.text, fontSize: type.size.md, lineHeight: type.leading.normal }}>
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
          padding: `${space.md}px ${space.xxl}px`,
          borderTop: `1px solid ${palette.border}`,
          background: legacy.errorBg,
        }}>
        <div style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.wider, marginBottom: space.xs }}>
        ERROR
        </div>
        <div style={{
          color: palette.red,
          fontSize: type.size.sm,
          lineHeight: type.leading.snug,
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
        padding: `${space.sm}px ${space.xxl}px`,
        borderTop: `1px solid ${palette.border}`,
        overflow: "hidden",
      }}>
      <div style={{
        color: palette.dim,
        fontSize: type.size.sm,
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
          padding: `${space.md}px ${space.xxl}px`,
          borderTop: `1px solid ${palette.border}`,
          display: "flex",
          gap: space.sm,
          justifyContent: "flex-end",
          background: palette.card,
        }}>
        {onDismiss && (
          <Btn label="DISMISS" color={palette.muted} onClick={onDismiss} />
        )}
        {onRetry && (
          item.status === "dry_run" ? (
            <Btn label="▶ PROCESS NOW" color={palette.green} bg={alpha(palette.green, ALPHA.low)} onClick={onRetry} />
          ) : (
            <Btn label={`↻ ${retryLabel}`} color={palette.amber} bg={alpha(palette.amber, ALPHA.low)} onClick={onRetry} />
          )
        )}
        </div>
      )}
      </div>
      </div>
  );
};
