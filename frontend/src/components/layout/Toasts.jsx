import { C } from "../../constants";

/* ═══════════════════════════════════════════════════════════════════════════
   TOAST NOTIFICATIONS
   NOTE: the cap-at-8 logic and 5s auto-dismiss timer live in the parent's
   `toast()` function (App.jsx / useAppData), not here — this component is a
   pure renderer of whatever `items` array it is given.
═══════════════════════════════════════════════════════════════════════════ */
export const Toasts = ({ items, isMobile = false }) => (
  <div style={{
    position: "fixed",
    bottom: 20,
    // Desktop: bottom-right corner.
    // Mobile: bottom-centre so toasts don't overflow a narrow screen.
    ...(isMobile
      ? { left: "50%", transform: "translateX(-50%)", right: "auto" }
      : { right: 20 }
    ),
    display: "flex",
    flexDirection: "column",
    gap: 6,
    zIndex: 2000,
    pointerEvents: "none",
    width: isMobile ? "calc(100vw - 32px)" : "auto",
  }}>
    {items.map(t => (
      <div
        key={t.id}
        style={{
          padding: "8px 14px",
          background: C.card,
          border: `1px solid ${t.color || C.border}`,
          borderLeft: `3px solid ${t.color || C.amber}`,
          color: C.text,
          fontSize: 11,
          minWidth: isMobile ? "auto" : 210,
          maxWidth: isMobile ? "none" : 360,
          lineHeight: 1.5,
          animation: "toastIn 0.2s ease",
        }}
      >
        {t.msg}
      </div>
    ))}
  </div>
);
