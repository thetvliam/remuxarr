import { useTheme } from "../../theme";
import { LanguageReviewSection } from "./LanguageReviewSection";

/* ═══════════════════════════════════════════════════════════════════════════
 * AUDIO LANGUAGE REVIEW SECTION
 *
 * Files whose kept audio track carries a language tag that does not match the
 * user's preferred languages — a defined-but-wrong tag, as distinct from the
 * subtitle list's undefined ones.
 *
 * Distinct from the manual-review list above it: files here are already fully
 * processed and playable, so this is purely an optional correction workflow.
 *
 * All the mechanics live in LanguageReviewSection. This file is the
 * configuration and the copy, which is the whole of what makes this list
 * different from the subtitle one.
 ═══════════════════════════════════════════════════════════════════════════ */
export const AudioLanguageReviewSection = (props) => {
  const { palette } = useTheme();
  return (
    <LanguageReviewSection
      {...props}
      endpoint="/api/audio-language-review/"
      accent={palette.blue}
      glyph="♪"
      heading="AUDIO LANGUAGE REVIEW"
      trackNoun="audio language"
      filterTitle="Filter by the language tag currently on the file"
      emptyMessage="No audio language mismatches found ✓"
      blurb={
        <>
          Files whose kept audio track has a language tag that doesn&apos;t match
          your preferred languages — e.g. an English show mistagged with a
          different language. These files are already fully processed and
          playable; this is optional. Search a show name to select every
          flagged episode at once, then either set the correct language and
          reprocess, or confirm the current tag is already correct (e.g.
          genuinely foreign-language content) to stop it being flagged again.
        </>
      }
    />
  );
};
