import { useTheme, LAYER } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * TOAST NOTIFICATIONS
 * NOTE: the cap-at-8 logic and 5s auto-dismiss timer live in the parent's
 * `toast()` function (App.jsx / useAppData), not here — this component is a
 * pure renderer of whatever `items` array it is given.
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const Toasts = ({ items, isMobile = false }) => {
  const { palette, type, space, radius, size, toastTone } = useTheme();

  /* Toasts carry a tone name, not a colour, and it is resolved here — at
   * render, from the theme that is current at render. An unrecognised tone
   * falls back to the accent rather than throwing or rendering an invisible
   * border: a mistyped tone should look slightly wrong, not break the only
   * channel the app has for telling you something failed. */
  const colorFor = (tone) => toastTone[tone] || palette.amber;

  return (
    /* A live region, so toasts are announced rather than only drawn. This is
     * the app's only channel for reporting that something failed — a bulk
     * apply that 500'd, a setting that would not save, a job that errored —
     * and without it a screen reader user got no indication any of that had
     * happened at all.
     *
     * polite rather than assertive: these report on background work and
     * should wait for a pause rather than cut across whatever the user is
     * reading. The container is always mounted, not conditional on there
     * being toasts, because a live region has to exist before content is
     * put into it for the insertion to be announced. */
    <div
    role="status"
    aria-live="polite"
    aria-atomic="false"
    style={{
      position: "fixed",
      bottom: size.toastOffset,
      // Desktop: bottom-right corner.
      // Mobile: bottom-centre so toasts don't overflow a narrow screen.
      ...(isMobile
      ? { left: "50%", transform: "translateX(-50%)", right: "auto" }
      : { right: size.toastOffset }
      ),
      display: "flex",
      flexDirection: "column",
      gap: space.xs,
      zIndex: LAYER.toast,
      pointerEvents: "none",
      width: isMobile ? `calc(100vw - ${size.toastMobileInset}px)` : "auto",
    }}>
    {items.map(t => (
      <div
      key={t.id}
      style={{
        padding: `${space.sm}px ${space.xl}px`,
        background: palette.card,
        border: `1px solid ${colorFor(t.tone)}`,
                     borderLeft: `${size.toastAccent}px solid ${colorFor(t.tone)}`,
                     borderRadius: radius.sm,
                     color: palette.text,
                     fontSize: type.size.md,
                     minWidth: isMobile ? "auto" : size.toastMinW,
                     maxWidth: isMobile ? "none" : size.toastMaxW,
                     lineHeight: type.leading.tight,
                     animation: "toastIn 0.2s ease",
      }}
      >
      {t.msg}
      </div>
    ))}
    </div>
  );
};
