#!/usr/bin/env python3
"""
Post-process the TOEIC manifest:

1. MERGE word-level segments → proper sentences. Some stretches of CD 6/7
   come back from Whisper at word granularity ("I" / "noticed" / "that"
   each as a separate sentence). Merge by accumulating text until sentence-
   ending punctuation.

2. DEDUPE hallucination repeats. Whisper sometimes gets stuck and emits the
   same short phrase 10+ times on silence (e.g. "D. They're standing near
   the table." × 11 with suspicious 0.2s durations). Collapse consecutive
   identical-text spans into one sentence spanning the full time range.

3. SPLIT TOEIC structural markers into separate sentences:
     "Number 9. Look at the picture marked number 9..."
       → ["Number 9.", "Look at the picture marked number 9..."]
     "statement A. They're standing by the table. B. They're seated..."
       → ["statement A. They're standing by the table.",
          "B. They're seated..."]

4. REGROUP paragraphs by silence-gap so each question stays coherent.

Usage:
    python3 scripts/clean_toeic.py web/library/toeic-listening/manifest.json
"""

import json
import re
import sys
from pathlib import Path


SENT_END = re.compile(r"[.?!]\s*$")
# TOEIC structural split markers. Word-level Whisper output has no
# punctuation, so we split on content-level keywords (with or without ".").
# Each alternative is a zero-width anchor on a space — re.split eats the
# space and emits the right-hand side as the start of the next segment.
TOEIC_SPLITS = re.compile(
    # Each alternative matches a single space character. Zero-width anchors
    # on either side enforce context. The leading-space before each `|` is
    # deliberately absent — adding one would consume the space BEFORE the
    # lookbehind could see the character before it, silently breaking the match.
    # Some CDs come back from Whisper fully word-level and lowercased, so
    # every structural marker has an inline `(?i)` to accept either case.
    # Skip "marked number N" (picture-label phrase, not a new question) via
    # 7-char negative lookbehind on the preceding word.
    r"(?<=\w)(?<!marked) (?=(?i:Number) \d+\b)"
    r"|(?<=\w)(?<!marked) (?=(?i:Number) (?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b)"
    r"|(?<=\w) (?=(?i:Questions) \d+ (?i:through))"
    r"|(?<=\w) (?=(?i:Part) \d+\b)"
    r"|(?<=\w) (?=(?i:Directions)\b)"
    r"|(?<=\w) (?=(?i:Look at the picture))"
    r"|(?<=\w) (?=(?i:Go on to))"
    r"|(?<=\w) (?=(?i:Now, listen to the))"
    r"|(?<=\w) (?=[A-D]\. [A-Z])"           # "...text A. The pilots..."
    r"|(?<=\w) (?=[A-D] [A-Z][a-z])"        # "...text A The pilots..."
    # Lowercase option letters ARE ambiguous ("take a look"), so require the
    # next token to be a pronoun / contraction / common option-start word.
    r"|(?<=\w) (?=[a-d] (?:he|she|it|they|we|you|I|a|an|the|some|many|most|"
    r"both|either|there|this|that|these|those|"
    r"\w+'(?:s|re|ve|ll|d|m))\b)"
    r"|(?<=conversation) (?=[A-Z])"
    r"|(?<=announcement) (?=[A-Z])"
    r"|(?<=talk) (?=[A-Z])"
    r"|(?<=[a-z]\?) (?=[A-Z])"
    r"|(?<=[a-z]\.) (?=[A-Z])"
)

PARAGRAPH_GAP = 1.5  # seconds; merge sentences into paragraphs when gap < this


def merge_word_level(sents: list) -> list:
    """Merge word-level segments until we see sentence-ending punctuation.

    Detection: if the average text length across a chapter's sentences is
    < 15 chars, Whisper emitted words. Merge them.
    Merge is bounded by sentence-ending punctuation OR a big time gap
    (otherwise two runs of word-level "sentences" can glue together).
    """
    if not sents:
        return sents
    avg_len = sum(len(s["text"]) for s in sents) / len(sents)
    if avg_len > 25:
        return sents

    # For word-level Whisper output, silence gaps are NOT reliable sentence
    # boundaries — the narrator pauses mid-option too. Instead we let the
    # structural-splits pass downstream segment by TOEIC markers (A./B./C./D.,
    # Number N, etc.). Here we merge aggressively so full passages arrive at
    # that pass as single strings.
    MAX_MERGED_DUR = 30.0
    GAP_FLUSH = 1.5
    out = []
    cur = None
    for s in sents:
        if cur is None:
            cur = {"text": s["text"], "start": s["start"], "end": s["end"]}
            continue
        # Flush on silence gap (Whisper drops punctuation, so this is our
        # only signal for sentence boundaries in word-level output).
        if s["start"] - cur["end"] > GAP_FLUSH:
            out.append(cur)
            cur = {"text": s["text"], "start": s["start"], "end": s["end"]}
            continue
        cur["text"] = (cur["text"].rstrip() + " " + s["text"].lstrip()).strip()
        cur["end"] = s["end"]
        # Flush on explicit sentence-end punctuation OR duration cap
        if SENT_END.search(cur["text"]) or (cur["end"] - cur["start"]) > MAX_MERGED_DUR:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def glue_option_fragments(sents: list) -> list:
    """Merge single-letter 'A'/'B'/'C'/'D' fragments with the next sentence.
    Word-level Whisper output sometimes emits the option letter as its own
    word-segment (with a pause before the option content), so the merge pass
    flushes after the letter. Re-glue them here."""
    LETTER = re.compile(r"^[A-Da-d]\.?$")
    out = []
    for s in sents:
        if out:
            prev = out[-1]
            if LETTER.match(prev["text"].strip()):
                prev["text"] = prev["text"].rstrip() + " " + s["text"].lstrip()
                prev["end"] = s["end"]
                continue
        out.append(s)
    return out


def dedupe_hallucinations(sents: list) -> list:
    """Collapse consecutive identical-text spans into a single merged span."""
    out = []
    for s in sents:
        key = s["text"].strip().lower()
        if out and out[-1]["text"].strip().lower() == key:
            prev = out[-1]
            prev["end"] = max(prev["end"], s["end"])
            continue
        out.append(s)
    return out


def split_toeic_structure(sents: list) -> list:
    """Split sentences at TOEIC structural markers — A./B./C./D. options,
    'Number N', 'Look at the picture', 'Go on to the next page', etc."""
    out = []
    for s in sents:
        parts = TOEIC_SPLITS.split(s["text"])
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) <= 1:
            out.append(s)
            continue
        # Distribute the sentence's time range proportionally by char count
        total = sum(len(p) for p in parts) or 1
        t = s["start"]
        dur_total = s["end"] - s["start"]
        for p in parts:
            frac = len(p) / total
            t_end = t + dur_total * frac
            out.append({"text": p, "start": round(t, 3), "end": round(t_end, 3)})
            t = t_end
    return out


def rebuild_paragraphs(sents: list, chapter_idx: int) -> list:
    """Regroup a chapter's flat sentence list into paragraphs.

    A new paragraph starts when either:
      - silence gap > PARAGRAPH_GAP seconds, OR
      - the sentence begins with a TOEIC structural marker that signals
        a new question / section ("Number 9", "Questions 47 through 49",
        "Part 3", "Directions", "Go on to the next page", etc.)
    """
    # Start a new paragraph ONLY at top-level section markers. Within a
    # question ("Number N. Look at the picture... A. ... B. ... C. ... D. ...")
    # everything stays in one paragraph so the visual grouping matches the
    # logical one.
    NUM_WORD = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|" \
               r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
    PARA_START_RE = re.compile(
        rf"^(?:Number (?:\d+|{NUM_WORD})|Questions \d+ through \d+|"
        rf"Part \d+|Directions|Now,? part\b|This is the end)\b",
        re.IGNORECASE,
    )
    paragraphs = []
    cur = []
    prev_end = None
    for s in sents:
        starts_new = (
            (prev_end is not None and s["start"] - prev_end > PARAGRAPH_GAP)
            or PARA_START_RE.match(s["text"])
        )
        if starts_new and cur:
            paragraphs.append(cur)
            cur = []
        cur.append(s)
        prev_end = s["end"]
    if cur:
        paragraphs.append(cur)

    out = []
    for pi, psents in enumerate(paragraphs):
        pid = f"{chapter_idx}.{pi}"
        out.append({
            "id": pid,
            "start": psents[0]["start"],
            "end": psents[-1]["end"],
            "sentences": [
                {
                    "id": f"{pid}.{si}",
                    "text": s["text"],
                    "start": s["start"],
                    "end": s["end"],
                    "match_ratio": 1.0,
                }
                for si, s in enumerate(psents)
            ],
        })
    return out


def main():
    mf_path = Path(sys.argv[1])
    manifest = json.loads(mf_path.read_text())

    totals = {"before_sents": 0, "after_sents": 0,
              "merged": 0, "deduped": 0, "split": 0}

    for ch in manifest["chapters"]:
        sents = [s for p in ch["paragraphs"] for s in p["sentences"]]
        totals["before_sents"] += len(sents)
        before = len(sents)

        sents = merge_word_level(sents)
        after_merge = len(sents)
        totals["merged"] += (before - after_merge)

        sents = glue_option_fragments(sents)
        after_glue = len(sents)
        totals["merged"] += (after_merge - after_glue)
        after_merge = after_glue

        sents = dedupe_hallucinations(sents)
        after_dedupe = len(sents)
        totals["deduped"] += (after_merge - after_dedupe)

        sents = split_toeic_structure(sents)
        after_split = len(sents)
        totals["split"] += (after_split - after_dedupe)

        ch["paragraphs"] = rebuild_paragraphs(sents, ch["id"])
        totals["after_sents"] += len(sents)
        print(f"  {ch['title']:6}  {before:>5} → {len(sents):>5} sentences  "
              f"(merged -{before - after_merge}, "
              f"deduped -{after_merge - after_dedupe}, "
              f"split +{after_split - after_dedupe})")

    # Re-roll chapter bounds
    for ch in manifest["chapters"]:
        ch_sents = [s for p in ch["paragraphs"] for s in p["sentences"]]
        if ch_sents:
            ch["start"] = min(s["start"] for s in ch_sents)
            ch["end"] = max(s["end"] for s in ch_sents)

    mf_path.write_text(json.dumps(manifest, ensure_ascii=False))
    print()
    print(f"Total sentences: {totals['before_sents']} → {totals['after_sents']}")
    print(f"  word-level merges: -{totals['merged']}")
    print(f"  hallucination dedupes: -{totals['deduped']}")
    print(f"  structural splits: +{totals['split']}")


if __name__ == "__main__":
    main()
