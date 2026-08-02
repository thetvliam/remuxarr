import { useState, useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import { fmtTime } from "../../utils";
import { LED } from "../atoms/LED";
import { EmptyState } from "../atoms/EmptyState";
import { PanelHeader } from "../layout/PanelHeader";

/* ═══════════════════════════════════════════════════════════════════════════
 * QUEUE ROW
 * Shows per-item ↑ TOP and × buttons on hover. Only pending items reach
 * this component (the parent filters out processing items), so there's
 * no processing/progress state here.
 ═ * * * ═*═════════════════════════════════════════════════════════════════════════ */
const QueueRow = ({ item, onSelect, onDismiss, onPrioritize, hasHover }) => {
    const { palette, type, space, radius, size, surface, statusColor } = useTheme();
    const [hover, setHover] = useState(false);
    const f = item.file || {};

    const stopProp = (fn) => (e) => { e.stopPropagation(); fn(); };

    /* Revealed on hover to keep the row quiet — but only where hovering is
     * possible. On a touch device there is no hover, so these sat at
     * opacity 0 permanently and the two per-row actions could not be
     * reached at all. Worse than invisible: opacity 0 still takes pointer
     * events, so they remained tappable, two unlabelled hit targets inside
     * the row's own tap area. A stray tap dismissed a queue item or
     * reordered the queue with nothing on screen to explain it.
     *
     * hasHover is a capability, not a width — a tablet with a trackpad
     * hovers and a narrow desktop window hovers, so isMobile is the wrong
     * question. pointerEvents follows visibility so an invisible control
     * is never clickable either. */
    const shown = hover || !hasHover;
    const actionBtn = (label, color, fn, title) => (
        <button
        onClick={stopProp(fn)}
        // title is a tooltip and never surfaces on touch; the visible label
        // is "×" on one of these, which reads as nothing useful aloud. The
        // title text is already a full description, so it doubles as the
        // accessible name.
        aria-label={title}
        title={title}
        style={{
            background: "none",
            border: `1px solid ${alpha(color, ALPHA.heavy)}`,
                                                    borderRadius: radius.sm,
                                                    color,
                                                    fontSize: type.size.xs,
                                                    fontFamily: type.family,
                                                    letterSpacing: type.tracking.normal,
                                                    padding: `${space.hair}px ${space.xs}px`,
                                                    cursor: "pointer",
                                                    flexShrink: 0,
                                                    opacity: shown ? 1 : 0,
                                                    pointerEvents: shown ? "auto" : "none",
                                                    transition: "opacity 0.1s",
        }}
        >
        {label}
        </button>
    );

    return (
        /* A div with button semantics rather than a real <button>, because
         * this row contains its own ↑ TOP and × buttons. A <button> may not
         * contain interactive content: the parser is entitled to hoist the
         * inner buttons out, and the outer button swallows their focus
         * semantics, so keyboard users could not reach them at all.
         * stopPropagation fixes the click bubbling but not the structure.
         *
         * role + tabIndex + the Enter/Space handler restore what the real
         * element gave for free. Space is preventDefault'ed because its
         * default action on a focused element is to scroll the page. */
        <div
        role="button"
        tabIndex={0}
        onClick={() => onSelect(item)}
        onKeyDown={e => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(item);
            }
        }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            padding: `${space.md}px ${space.xl}px`,
            background: hover ? surface.rowHoverBg : "transparent",
            border: "none",
            borderBottom: `1px solid ${palette.border}`,
            cursor: "pointer",
            fontFamily: type.family,
        }}
        >
        {/* Row: LED + name + action buttons + time */}
        <div style={{
            display: "flex", alignItems: "center",
            gap: space.xs, marginBottom: space.xxs,
        }}>
        <LED
        color={statusColor[item.status] || palette.dim}
        pulse={false}
        size={size.ledSizeSm}
        />
        <span style={{
            color: palette.text,
            fontSize: type.size.base,
            fontWeight: type.weight.medium,
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            minWidth: 0,
        }}>
        {f.filename || "—"}
        </span>

        {/* Per-row actions (this row is always a pending item) */}
        <div style={{ display: "flex", gap: space.xxs, alignItems: "center" }}>
        {actionBtn("↑ TOP", palette.amber, () => onPrioritize(item), "Move to top of queue")}
        {actionBtn("×", palette.red, () => onDismiss(item), "Remove from queue")}
        </div>

        <span style={{ color: palette.dim, fontSize: type.size.xs, flexShrink: 0 }}>
        {fmtTime(item.created_at)}
        </span>
        </div>

        {/* Reason */}
        <div style={{
            color: palette.muted,
            fontSize: type.size.sm,
            paddingLeft: space.xl,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
        }}>
        {item.reason || "—"}
        </div>
        </div>
    );
};

/* ═══════════════════════════════════════════════════════════════════════════
 * QUEUE PANEL
 ═ * * * ═*═════════════════════════════════════════════════════════════════════════ */
export const QueuePanel = ({ items, onSelect, onDismiss, onClear, onPrioritize }) => {
    const { palette, type, space, radius } = useTheme();
    // Resolved once here rather than per row: a long queue would otherwise
    // register a media-query listener for every visible item.
    const { hasHover } = useBreakpoint();
    const [search,     setSearch]     = useState("");
    const [clearArmed, setClearArmed] = useState(false);

    const pendingCount = items.filter(i => i.status === "pending").length;
    const filtered     = search.trim()
    ? items.filter(i =>
    (i.file?.filename || "").toLowerCase().includes(search.trim().toLowerCase())
    )
    : items;

    // Auto-disarm after 3 seconds if the user doesn't confirm. Keyed on the
    // armed flag with cleanup, matching DangerZone and MaintenanceSection —
    // a bare setTimeout in the handler stacked one timer per click and each
    // survived unmount, so a timer from a previous mount could disarm a
    // freshly armed button. Same pattern DangerZone's comment calls critical.
    useEffect(() => {
        if (!clearArmed) return;
        const t = setTimeout(() => setClearArmed(false), 3000);
        return () => clearTimeout(t);
    }, [clearArmed]);

    const handleClear = () => {
        if (!clearArmed) {
            setClearArmed(true);
        } else {
            setClearArmed(false);
            onClear();
        }
    };

    const right = pendingCount > 0 ? (
        <button
        onClick={handleClear}
        title={clearArmed ? "Click again to confirm" : "Remove all pending items from queue"}
        style={{
            padding: `${space.hair}px ${space.md}px`,
            background: clearArmed ? alpha(palette.red, ALPHA.medium) : "transparent",
                                      border: `1px solid ${clearArmed ? palette.red : palette.border}`,
                                      borderRadius: radius.sm,
                                      color: clearArmed ? palette.red : palette.dim,
                                      fontSize: type.size.xs,
                                      fontFamily: type.family,
                                      letterSpacing: type.tracking.wide,
                                      cursor: "pointer",
                                      transition: "all 0.15s",
        }}
        >
        {clearArmed ? "CONFIRM CLEAR" : "CLEAR QUEUE"}
        </button>
    ) : null;

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
        <PanelHeader
        label="QUEUE"
        count={search.trim() ? `${filtered.length}/${items.length}` : items.length}
        right={right}
        />

        {/* Search */}
        {items.length > 0 && (
            <div style={{
                padding: `${space.xs}px ${space.lg}px`,
                borderBottom: `1px solid ${palette.border}`,
                flexShrink: 0,
            }}>
            <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter by filename…"
            style={{
                width: "100%",
                padding: `${space.xxs}px ${space.sm}px`,
                background: palette.bg,
                border: `1px solid ${palette.border}`,
                borderRadius: radius.sm,
                color: palette.text,
                fontSize: type.size.md,
                fontFamily: type.family,
            }}
            />
            </div>
        )}

        <div style={{ flex: 1, overflowY: "auto" }}>
        {items.length === 0 ? (
            <EmptyState msg="Queue is empty" />
        ) : filtered.length === 0 ? (
            <EmptyState msg={`No items match "${search}"`} />
        ) : (
            filtered.map(item => (
                <QueueRow
                key={item.id}
                item={item}
                onSelect={onSelect}
                onDismiss={onDismiss}
                onPrioritize={onPrioritize}
                hasHover={hasHover}
                />
            ))
        )}
        </div>
        </div>
    );
};
