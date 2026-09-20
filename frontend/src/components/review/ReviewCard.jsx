import { useState } from "react";

import { useTheme } from "../../theme";
import { Btn } from "../atoms/Btn";

/* ═══════════════════════════════════════════════════════════════════════════
 * REVIEW CARD
 *
 * One question, asked once, about every file it applies to. A release of one
 * show is twelve files with the same tracks, and answering it twelve times is
 * the same answer twelve times.
 *
 * Presentational. The staged answers, the skip and the outcome line all come
 * in as props and every change goes out as a callback, so the page owns what
 * has been decided and this owns how it reads.
 *
 * The outcome line especially: it says what Apply would do to these files,
 * and it cannot be worked out from the answers. Keeping a track holds a file
 * as MKV, but so does DTS audio, and a file already in MP4 converts to
 * nothing. The server computes it from the decision engine and it arrives
 * here as a string.
 *
 * A track's choices come from its own reason. A bitmap has no text to
 * extract, and extracting a track whose extraction just failed would only
 * fail again, so those two are answered Keep or Delete and nothing else.
 * ═══════════════════════════════════════════════════════════════════════════ */

/* "Delete" is what the page calls it; "remove" is what the server stores.
 * Keeping the two apart here means the wording can change without touching
 * an endpoint, and the API keeps the name its other callers already use. */
const CHOICES = {
    keep:    { label: "Keep",        tone: "yellow" },
    extract: { label: "Extract SRT", tone: "blue" },
    remove:  { label: "Delete",      tone: "red" },
};

const CHOICES_FOR_REASON = {
    image:    ["keep", "remove"],
    encoding: ["keep", "remove"],
};
const DEFAULT_CHOICES = ["keep", "extract", "remove"];

const REASON_LABEL = {
    image:    "image, no text to extract",
    styled:   "styled",
    encoding: "encoding, extraction failed",
};

export const choicesForTrack = track =>
    CHOICES_FOR_REASON[track.reason] || DEFAULT_CHOICES;

const trackName = (track, index) => track.title || `Track ${index + 1}`;

const describe = track => [
    track.language || "und",
    track.codec,
    REASON_LABEL[track.reason] || track.reason,
].filter(Boolean).join(" · ");

/* Extract is a third outcome, not a shade of the other two: the track leaves
 * the file, like Delete, but its text survives beside it. Blue rather than
 * the theme's accent, which is amber and sits right next to the yellow Keep
 * uses — two choices that look the same are worse than no colour at all. */
const TONES = { yellow: "yellow", blue: "blue", red: "red" };

const ChoiceRow = ({ choices, selected, onChoose, palette, space }) => (
    <div style={{ display: "flex", gap: space.xs, flexWrap: "wrap" }}>
    {choices.map(choice => {
        const { label, tone } = CHOICES[choice];
        const isSelected = selected === choice;
        return (
            <Btn
            key={choice}
            label={label}
            pressed={isSelected}
            color={palette[TONES[tone]]}
            bg={isSelected ? palette[TONES[tone]] + "22" : "transparent"}
            onClick={() => onChoose(choice)}
            />
        );
    })}
    </div>
);

export const ReviewCard = ({
    group,
    answers = {},          // slot index -> choice, for every file in the card
    fileAnswers = {},      // file_id -> { slot index -> choice }
    skipped = false,
    outcome = null,
    onChoose,
    onChooseForFile,
    onUseGroupChoice,
    onToggleSkip,
}) => {
    const { palette, type, space, radius, size, surface } = useTheme();
    const [showFiles, setShowFiles] = useState(false);
    const [openFile, setOpenFile] = useState(null);

    const tracks = group.tracks || [];
    const files = group.files || [];
    const fileCount = group.file_count ?? files.length;

    return (
        <div style={{
            padding: space.xl,
            background: palette.card,
            border: `1px solid ${surface.reviewBorder}`,
            borderRadius: radius.sm,
            borderLeft: `${size.accentWidth}px solid ${skipped ? palette.dim : palette.yellow}`,
            marginBottom: space.md,
            opacity: skipped ? 0.6 : 1,
        }}>

        <div style={{ display: "flex", alignItems: "flex-start", gap: space.lg }}>
        <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
            color: palette.text,
            fontSize: type.size.lg,
            fontWeight: type.weight.semibold,
        }}>
        {group.heading}
        </div>
        <div style={{ color: palette.dim, fontSize: type.size.sm, marginTop: space.xxs }}>
        {fileCount === 1 ? "1 file" : `${fileCount} files`}
        {group.font_attachments
            ? ` · ${group.font_attachments} embedded fonts`
            : ""}
        </div>
        </div>
        <Btn
        label={skipped ? "Skipped" : "Skip"}
        color={skipped ? palette.dim : palette.text}
        onClick={onToggleSkip}
        />
        </div>

        {skipped ? (
            <div style={{ color: palette.dim, fontSize: type.size.md, marginTop: space.md }}>
            Skipped. These files are asked about again on the next scan.
            </div>
        ) : (
            <>
            <div style={{ marginTop: space.lg }}>
            {tracks.map((track, slot) => (
                <div key={slot} style={{ marginBottom: space.md }}>
                <div style={{ color: palette.text, fontSize: type.size.md }}>
                {trackName(track, slot)}
                </div>
                <div style={{
                    color: palette.dim,
                    fontSize: type.size.sm,
                    marginBottom: space.xs,
                }}>
                {describe(track)}
                </div>
                <ChoiceRow
                choices={choicesForTrack(track)}
                selected={answers[slot]}
                onChoose={choice => onChoose(slot, choice)}
                palette={palette}
                space={space}
                />
                </div>
            ))}
            </div>

            {outcome && (
                <div style={{
                    color: palette.yellow,
                    fontSize: type.size.md,
                    lineHeight: type.leading.snug,
                    marginTop: space.md,
                }}>
                {outcome}
                </div>
            )}
            </>
        )}

        <div style={{ marginTop: space.md }}>
        <Btn
        label={showFiles ? `Hide files (${fileCount})` : `Show files (${fileCount})`}
        color={palette.dim}
        onClick={() => setShowFiles(v => !v)}
        />
        </div>

        {showFiles && (
            <div style={{ marginTop: space.sm }}>
            {files.map(file => {
                const own = fileAnswers[file.file_id];
                const isOpen = openFile === file.file_id;
                return (
                    <div key={file.file_id} style={{
                        borderTop: `1px solid ${surface.reviewBorder}`,
                        paddingTop: space.xs,
                        paddingBottom: space.xs,
                    }}>
                    <button
                    onClick={() => setOpenFile(isOpen ? null : file.file_id)}
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: space.sm,
                        width: "100%",
                        padding: 0,
                        background: "transparent",
                        border: "none",
                        color: palette.text,
                        fontSize: type.size.sm,
                        fontFamily: type.family,
                        textAlign: "left",
                        cursor: "pointer",
                    }}
                    >
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden",
                        textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {file.filename}
                    </span>
                    {own && (
                        <span style={{ color: palette.blue, fontSize: type.size.xs }}>
                        Set on its own
                        </span>
                    )}
                    </button>

                    {isOpen && !skipped && (
                        <div style={{ paddingTop: space.xs, paddingBottom: space.xs }}>
                        {tracks.map((track, slot) => (
                            <div key={slot} style={{ marginBottom: space.xs }}>
                            <div style={{ color: palette.dim, fontSize: type.size.xs,
                                marginBottom: space.xxs }}>
                            {trackName(track, slot)}
                            </div>
                            <ChoiceRow
                            choices={choicesForTrack(track)}
                            /* This file's own answer, never the card's: a row
                             * showing the group's choice as its own would say
                             * the file had been set when it had not. */
                            selected={own ? own[slot] : undefined}
                            onChoose={choice => onChooseForFile(file.file_id, slot, choice)}
                            palette={palette}
                            space={space}
                            />
                            </div>
                        ))}
                        {own && (
                            <Btn
                            label="Use group choice"
                            color={palette.dim}
                            onClick={() => onUseGroupChoice(file.file_id)}
                            />
                        )}
                        </div>
                    )}
                    </div>
                );
            })}
            </div>
        )}
        </div>
    );
};
