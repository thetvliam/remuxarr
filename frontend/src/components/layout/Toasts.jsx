import { useTheme } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * TOAST NOTIFICATIONS
 * NOTE: the cap-at-8 logic and 5s auto-dismiss timer live in the parent's
 * `toast()` function (App.jsx / useAppData), not here — this component is a
 * pure renderer of whatever `items` array it is given.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const Toasts = ({ items, isMobile = false }) => {
  const { palette, type, space, radius, legacy } = useTheme();
  return (
    <div style={{
      position: "fixed",
      bottom: legacy.toastOffset,
      // Desktop: bottom-right corner.
      // Mobile: bottom-centre so toasts don't overflow a narrow screen.
      ...(isMobile
      ? { left: "50%", transform: "translateX(-50%)", right: "auto" }
      : { right: legacy.toastOffset }
      ),
      display: "flex",
      flexDirection: "column",
      gap: space.xs,
      zIndex: 2000,
      pointerEvents: "none",
      width: isMobile ? `calc(100vw - ${legacy.toastMobileInset}px)` : "auto",
    }}>
    {items.map(t => (
      <div
      key={t.id}
      style={{
        padding: `${legacy.toastPadY}px ${legacy.toastPadX}px`,
        background: palette.card,
        border: `1px solid ${t.color || palette.border}`,
        borderLeft: `${legacy.toastAccent}px solid ${t.color || palette.amber}`,
        borderRadius: radius.sm,
        color: palette.text,
        fontSize: type.size.md,
        minWidth: isMobile ? "auto" : legacy.toastMinW,
        maxWidth: isMobile ? "none" : legacy.toastMaxW,
        lineHeight: legacy.toastLine,
        animation: "toastIn 0.2s ease",
      }}
      >
      {t.msg}
      </div>
    ))}
    </div>
  );
};
