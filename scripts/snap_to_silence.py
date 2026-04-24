#!/usr/bin/env python3
"""
Snap manifest sentence boundaries to actual audio silences.

Whisper/CTC word timestamps are often off by 100-500ms at sentence
boundaries when the narrator speaks continuously — you hear the last
word of the previous sentence bleed into the loop of the current one,
or the first consonant gets cut off.

ffmpeg silencedetect gives us the ground truth of where silences are.
For each sentence we "snap":
  - start → end of the nearest silence right before it  (pushes start later
                                                          so we don't bleed
                                                          into prev sentence)
  - end   → start of the nearest silence right after it  (pushes end later
                                                          so we catch the tail
                                                          of the last word)

Usage:
    python3 scripts/snap_to_silence.py \
        web/library/one-hundred-years/manifest.json \
        data/ohy-silence.txt
"""

import json
import re
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path


SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+\.?\d*)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+\.?\d*)")

# How far we'll look to find a snapping silence. Too big → we might
# snap across a legitimate sentence boundary; too small → we miss cases
# where Whisper was 500ms off.
WINDOW_BEFORE_START = 1.2        # look up to 1.2s before a sentence's start for a silence-end
WINDOW_AFTER_START = 0.30        # or up to 300ms after, in case Whisper was slightly late
WINDOW_BEFORE_END = 0.30         # mirror for end
WINDOW_AFTER_END = 1.2
MICRO_GAP = 0.01                 # 10ms micro-gap so adjacent snaps don't land on the same ms


def parse_silences(path: Path):
    """Parse ffmpeg silencedetect output into [(start, end), ...]."""
    silences = []
    cur_start = None
    for line in path.read_text().splitlines():
        if "silence_start" in line:
            m = SILENCE_START_RE.search(line)
            if m:
                cur_start = float(m.group(1))
        elif "silence_end" in line:
            m = SILENCE_END_RE.search(line)
            if m and cur_start is not None:
                silences.append((cur_start, float(m.group(1))))
                cur_start = None
    return silences


def snap_manifest(manifest_path: Path, silence_path: Path):
    m = json.loads(manifest_path.read_text())
    silences = parse_silences(silence_path)
    if not silences:
        print("No silences parsed — aborting", file=sys.stderr)
        return
    # For fast lookup: sorted arrays of silence starts + ends
    sil_starts = [s for s, e in silences]
    sil_ends = [e for s, e in silences]
    print(f"Parsed {len(silences)} silences, "
          f"spanning {silences[0][0]:.1f}s to {silences[-1][1]:.1f}s")

    all_sents = [s for ch in m["chapters"] for pa in ch["paragraphs"]
                 for s in pa["sentences"]]
    snapped_start = 0
    snapped_end = 0

    for s in all_sents:
        orig_start = s["start"]
        orig_end = s["end"]

        # Snap start → end of nearest silence just before the sentence.
        # A silence's END is when speech resumes. We want to start playing
        # right after that silence ends.
        lo = orig_start - WINDOW_BEFORE_START
        hi = orig_start + WINDOW_AFTER_START
        # Find rightmost silence_end in [lo, hi]
        i = bisect_right(sil_ends, hi) - 1
        if i >= 0 and sil_ends[i] >= lo:
            new_start = sil_ends[i] + MICRO_GAP
            # Only move if it changes by more than 20ms (avoid churn on already-good boundaries)
            if abs(new_start - orig_start) > 0.02:
                s["start"] = round(new_start, 3)
                snapped_start += 1

        # Snap end → start of nearest silence just after the sentence.
        # A silence's START is when the last word's tail finishes.
        lo = orig_end - WINDOW_BEFORE_END
        hi = orig_end + WINDOW_AFTER_END
        i = bisect_left(sil_starts, lo)
        if i < len(sil_starts) and sil_starts[i] <= hi:
            new_end = sil_starts[i] - MICRO_GAP
            if abs(new_end - orig_end) > 0.02 and new_end > s["start"] + 0.2:
                s["end"] = round(new_end, 3)
                snapped_end += 1

    # Ensure no overlap between adjacent sentences
    overlaps_fixed = 0
    for i in range(len(all_sents) - 1):
        cur, nxt = all_sents[i], all_sents[i + 1]
        if cur["end"] > nxt["start"]:
            mid = (cur["end"] + nxt["start"]) / 2
            cur["end"] = round(mid - 0.025, 3)
            nxt["start"] = round(mid + 0.025, 3)
            overlaps_fixed += 1
        if cur["end"] - cur["start"] < 0.2:
            cur["end"] = round(cur["start"] + 0.2, 3)

    # Re-roll paragraph / chapter bounds
    for ch in m["chapters"]:
        for pa in ch["paragraphs"]:
            sents = [s for s in pa["sentences"] if s.get("start") is not None]
            if sents:
                pa["start"] = min(s["start"] for s in sents)
                pa["end"] = max(s["end"] for s in sents)
        ch_sents = [s for pa in ch["paragraphs"] for s in pa["sentences"]
                    if s.get("start") is not None]
        if ch_sents:
            ch["start"] = min(s["start"] for s in ch_sents)
            ch["end"] = max(s["end"] for s in ch_sents)

    manifest_path.write_text(json.dumps(m, ensure_ascii=False))
    print(f"Snapped {snapped_start} sentence starts to silence ends")
    print(f"Snapped {snapped_end} sentence ends to silence starts")
    print(f"Fixed {overlaps_fixed} overlapping adjacent pairs")


if __name__ == "__main__":
    manifest_path = Path(sys.argv[1])
    silence_path = Path(sys.argv[2])
    snap_manifest(manifest_path, silence_path)
