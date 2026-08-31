/* ═══════════════════════════════════════════════════════════════════════════
 * FONTS
 *
 * Every webfont any theme names, self-hosted and bundled. Imported once at
 * startup rather than swapped per theme, for three reasons:
 *
 *   The theme picker previews each theme using that theme's own tokens,
 *   including type.root. Loading only the active theme's font meant the
 *   preview of the theme you were NOT using fell back to a system face — so
 *   the preview misrepresented the single largest difference between the
 *   themes, which is the thing it exists to show.
 *
 *   Both families were fetched from the Google Fonts CDN. Remuxarr is
 *   typically deployed on a LAN, often on a machine with no outbound
 *   internet or behind a CSP that blocks third-party origins, where the
 *   request simply fails and the stack falls through to whatever the OS
 *   supplies — with nothing on screen to indicate why the app looks wrong.
 *   Bundling removes the external dependency and the round-trip.
 *
 *   Swapping a stylesheet on theme change meant a flash of fallback text
 *   while the new family downloaded.
 *
 * These are the VARIABLE builds, one file per family spanning the whole
 * weight axis, rather than one file per weight. That is smaller over the
 * wire — two requests totalling ~88KB against nine totalling ~200KB — but
 * the real reason is that it removes a failure mode.
 *
 * With per-weight files, every weight named in type.weight has to have a
 * matching import here, and a missing one does not fail loudly: the browser
 * synthesises it by mechanically thickening the nearest real face. That
 * looks subtly wrong in a way that is easy to miss and hard to attribute,
 * and it was already happening — terminal declared bold: 700 while only
 * 400/500/600 were being fetched, so the app's most-used weight (49 call
 * sites) was faux bold everywhere. A variable font covers the whole axis,
 * so any weight a theme names is real without anyone having to remember to
 * keep two files in step.
 *
 * Each import pulls in several unicode-range subsets — cyrillic, greek,
 * vietnamese, latin-ext, latin. Only the ones a page actually needs get
 * requested, so an English UI fetches latin alone; the rest sit unused in
 * the build output.
 */

/* type.root for terminal and paper. Axis spans 100-800, which is the full
 * range JetBrains Mono ships; terminal's black is 800 for that reason. */
import "@fontsource-variable/jetbrains-mono/wght.css";

/* type.root for soft, midnight, dawn and linen. Axis spans 100-900. */
import "@fontsource-variable/inter/wght.css";
