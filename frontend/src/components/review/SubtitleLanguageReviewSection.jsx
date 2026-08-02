import { useTheme } from "../../theme";
import { LanguageReviewSection } from "./LanguageReviewSection";

/* ═══════════════════════════════════════════════════════════════════════════
 * SUBTITLE LANGUAGE REVIEW SECTION
 *
 * Every row here originates from an undefined ("und") tag rather than a
 * defined-but-wrong one — see fix_undefined_language's "always ask" mode.
 * That is the substantive difference from the audio list; the mechanics are
 * identical and live in LanguageReviewSection.
 ═══════════════════════════════════════════════════════════════════════════ */
export const SubtitleLanguageReviewSection = (props) => {
  const { palette } = useTheme();
  return (
    <LanguageReviewSection
      {...props}
      endpoint="/api/subtitle-language-review/"
      /* The established subtitle colour in this codebase — the same one the
       * extract_subtitle action badge uses. Read from the palette rather than
       * hardcoded, so it follows the theme like every other colour. */
      accent={palette.cyan}
      glyph="▭"
      heading="SUBTITLE LANGUAGE REVIEW"
      trackNoun="subtitle language"
      filterTitle="Filter by the language tag currently on the subtitle track"
      emptyMessage="No undefined subtitle languages found ✓"
      blurb={
        <>
          Files whose kept subtitle track has an undefined language tag,
          flagged because Fix Undefined Language Tags is set to Always Ask.
          These files are already fully processed and playable; this is
          optional. Search a show name to select every flagged episode at
          once, then either set the correct language and reprocess, or
          confirm it&apos;s fine to leave the tag undefined.
        </>
      }
    />
  );
};
