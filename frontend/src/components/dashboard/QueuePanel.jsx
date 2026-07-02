import { useState } from "react";
import { C, STATUS_COLOR } from "../../constants";
import { fmtTime } from "../../utils";
import { LED } from "../atoms/LED";
import { EmptyState } from "../atoms/EmptyState";
import { MiniBar } from "../bars/MiniBar";
import { PanelHeader } from "../layout/PanelHeader";

/* ═══════════════════════════════════════════════════════════════════════════
 * QUEUE ROW
 * Shows per-item ↑ TOP and × buttons on hover.  Both are hidden for items
 * that are currently processing (can't interrupt a running job).
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
const QueueRow = ({ item, onSelect, onDismiss, onPrioritize }) => {
    const [hover, setHover] = useState(false);
    const f          = item.file || {};
    const processing = item.status === "processing";

    const stopProp = (fn) => (e) => { e.stopPropagation(); fn(); };

    const actionBtn = (label, color, fn, title) => (
        <button
        onClick={stopProp(fn)}
        title={title}
        style={{
            background: "none",
            border: `1px solid ${color}55`,
            color,
            fontSize: 9,
            fontFamily: "inherit",
            letterSpacing: "0.08em",
            padding: "1px 6px",
            cursor: "pointer",
            flexShrink: 0,
            opacity: hover ? 1 : 0,
            transition: "opacity 0.1s",
        }}
        >
        {label}
        </button>
    );

    return (
        <button
        onClick={() => onSelect(item)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            padding: "9px 14px",
            background: hover ? "#ffffff07" : "transparent",
            border: "none",
            borderBottom: `1px solid ${C.border}`,
            cursor: "pointer",
            fontFamily: "inherit",
        }}
        >
        {/* Row: LED + name + action buttons + time */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
        <LED
        color={STATUS_COLOR[item.status] || C.dim}
        pulse={processing}
        size={6}
        />
        <span style={{
            color: C.text,
            fontSize: 12,
            fontWeight: 500,
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            minWidth: 0,
        }}>
        {f.filename || "—"}
        </span>

        {/* Per-row actions — hidden while processing */}
        {!processing && (
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            {actionBtn("↑ TOP", C.amber, () => onPrioritize(item), "Move to top of queue")}
            {actionBtn("×", C.red, () => onDismiss(item), "Remove from queue")}
            </div>
        )}

        <span style={{ color: C.dim, fontSize: 9, flexShrink: 0 }}>
        {fmtTime(item.created_at)}
        </span>
        </div>

        {/* Reason */}
        <div style={{
            color: C.muted,
            fontSize: 10,
            paddingLeft: 14,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
        }}>
        {item.reason || "—"}
        </div>

        {/* Mini progress bar (when actively processing) */}
        {processing && (
            <div style={{ paddingLeft: 14, marginTop: 5 }}>
            <MiniBar value={item.progress || 0} />
            </div>
        )}
        </button>
    );
};

/* ═══════════════════════════════════════════════════════════════════════════
 * QUEUE PANEL
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const QueuePanel = ({ items, onSelect, onDismiss, onClear, onPrioritize }) => {
    const [search,    setSearch]    = useState("");
    const [clearArmed, setClearArmed] = useState(false);

    const pendingCount = items.filter(i => i.status === "pending").length;
    const filtered     = search.trim()
    ? items.filter(i =>
    (i.file?.filename || "").toLowerCase().includes(search.trim().toLowerCase())
    )
    : items;

    const handleClear = () => {
        if (!clearArmed) {
            setClearArmed(true);
            // Auto-disarm after 3 seconds if user doesn't confirm
            setTimeout(() => setClearArmed(false), 3000);
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
            padding: "2px 9px",
            background: clearArmed ? C.red + "22" : "transparent",
            border: `1px solid ${clearArmed ? C.red : C.border}`,
            color: clearArmed ? C.red : C.dim,
            fontSize: 9,
            fontFamily: "inherit",
            letterSpacing: "0.1em",
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
                padding: "6px 12px",
                borderBottom: `1px solid ${C.border}`,
                flexShrink: 0,
            }}>
            <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter by filename…"
            style={{
                width: "100%",
                padding: "4px 8px",
                background: C.bg,
                border: `1px solid ${C.border}`,
                color: C.text,
                fontSize: 11,
                fontFamily: "inherit",
                outline: "none",
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
                />
            ))
        )}
        </div>
        </div>
    );
};
