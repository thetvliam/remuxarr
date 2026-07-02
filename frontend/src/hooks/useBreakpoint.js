import { useState, useEffect } from "react";

/* ═══════════════════════════════════════════════════════════════════════════
   useBreakpoint
   Returns { isMobile } — true when viewport width is below 640px.

   640px is the single breakpoint for the entire app. No tablet tier, no CSS
   media queries scattered through component files. Every responsive layout
   decision reads this one boolean.

   Safe to call in multiple components simultaneously — each instance
   maintains its own listener but they all resolve to the same value.
   There's no SSR concern since this is a pure client-side SPA.
═══════════════════════════════════════════════════════════════════════════ */
export function useBreakpoint() {
  const [isMobile, setIsMobile] = useState(
    typeof window !== "undefined" ? window.innerWidth < 640 : false
  );

  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  return { isMobile };
}
