"""Align Whisper word-level timestamps to the book's sentences.

Strategy:
  1. Flatten the book into a sequence of normalized word tokens with back-refs
     (chapter_idx, paragraph_idx, sentence_idx, word_idx_within_sentence).
  2. Flatten Whisper output into a list of normalized word tokens with timings.
  3. Use difflib.SequenceMatcher to find the longest common subsequences.
  4. For each book sentence, collect timings from matched audio words that fall
     within it. start = min start of matched words; end = max end.
  5. For sentences with no matches, interpolate from neighbors.

Usage:
  python align.py data/book.json data/audio1.json data/audio2.json output/aligned.json
"""

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path


WORD_RE = re.compile(r"[a-zA-Z\u00c0-\u024f]+(?:'[a-zA-Z]+)?")


def normalize(word: str) -> str:
    return word.lower().strip("'\"'\u201c\u201d\u2018\u2019.,;:!?()[]{}-\u2014\u2013 ")


def tokenize_sentence(text: str) -> list[str]:
    return [normalize(m.group(0)) for m in WORD_RE.finditer(text)]


def flatten_book(book: dict) -> tuple[list[str], list[tuple[int, int, int, int]]]:
    """Returns (tokens, backrefs) where backrefs[i] = (ch_idx, pa_idx, se_idx, w_idx)."""
    tokens: list[str] = []
    backrefs: list[tuple[int, int, int, int]] = []
    for ci, ch in enumerate(book["chapters"]):
        for pi, pa in enumerate(ch["paragraphs"]):
            for si, se in enumerate(pa["sentences"]):
                for wi, tok in enumerate(tokenize_sentence(se["text"])):
                    if tok:
                        tokens.append(tok)
                        backrefs.append((ci, pi, si, wi))
    return tokens, backrefs


def flatten_audio(audio_jsons: list[dict]) -> list[dict]:
    """Concatenate audio word streams and normalize tokens."""
    out: list[dict] = []
    for aj in audio_jsons:
        for w in aj["words"]:
            tok = normalize(w["w"])
            if not tok:
                continue
            out.append({"tok": tok, "start": w["start"], "end": w["end"]})
    return out


def align(book_tokens: list[str], audio_words: list[dict]) -> list[tuple[int, float, float]]:
    """Returns [(book_token_idx, start, end), ...] for matched pairs."""
    audio_tokens = [aw["tok"] for aw in audio_words]
    sm = SequenceMatcher(a=book_tokens, b=audio_tokens, autojunk=False)
    matches = []
    for block in sm.get_matching_blocks():
        if block.size == 0:
            continue
        for k in range(block.size):
            bi = block.a + k
            ai = block.b + k
            matches.append((bi, audio_words[ai]["start"], audio_words[ai]["end"]))
    return matches


def collect_timings(book: dict, backrefs: list, matches: list) -> None:
    """Mutates book: each sentence gets 'start', 'end', 'match_ratio'."""
    # sentence_key -> list of (start, end)
    sent_times: dict[tuple[int, int, int], list[tuple[float, float]]] = {}
    sent_total: dict[tuple[int, int, int], int] = {}

    for ci, ch in enumerate(book["chapters"]):
        for pi, pa in enumerate(ch["paragraphs"]):
            for si, se in enumerate(pa["sentences"]):
                sent_total[(ci, pi, si)] = len(tokenize_sentence(se["text"]))

    for bi, start, end in matches:
        ci, pi, si, _ = backrefs[bi]
        sent_times.setdefault((ci, pi, si), []).append((start, end))

    # Assign start/end per sentence
    ordered_keys: list[tuple[int, int, int]] = []
    for ci, ch in enumerate(book["chapters"]):
        for pi, pa in enumerate(ch["paragraphs"]):
            for si, se in enumerate(pa["sentences"]):
                ordered_keys.append((ci, pi, si))
                times = sent_times.get((ci, pi, si), [])
                if times:
                    se["start"] = min(t[0] for t in times)
                    se["end"] = max(t[1] for t in times)
                    se["match_ratio"] = len(times) / max(1, sent_total[(ci, pi, si)])
                else:
                    se["start"] = None
                    se["end"] = None
                    se["match_ratio"] = 0.0

    # Interpolate missing sentences: fill with neighbor averages
    def sent_at(key):
        ci, pi, si = key
        return book["chapters"][ci]["paragraphs"][pi]["sentences"][si]

    # Forward-fill: if a sentence has no timing, use prev end / next start
    prev_end = 0.0
    for key in ordered_keys:
        s = sent_at(key)
        if s["start"] is None:
            s["start"] = prev_end
        if s["end"] is None:
            s["end"] = s["start"]
        prev_end = s["end"] if s["end"] else prev_end

    # Back-fill from next: if end < start (unset), use next start
    for i in range(len(ordered_keys) - 1):
        s = sent_at(ordered_keys[i])
        n = sent_at(ordered_keys[i + 1])
        if s["end"] < s["start"] + 0.01 and n["start"] > s["start"]:
            s["end"] = n["start"]

    # Trim sentence ends to leave a silent gap before the next sentence.
    # Whisper's word.end typically coincides with the next word.start in
    # continuous speech, so without this the repeat-loop bleeds a fragment
    # of the next word into the current sentence.
    BOUNDARY_GAP = 0.12
    MIN_LEN = 0.2
    for i in range(len(ordered_keys) - 1):
        s = sent_at(ordered_keys[i])
        n = sent_at(ordered_keys[i + 1])
        if s.get("start") is None or s.get("end") is None or n.get("start") is None:
            continue
        target = n["start"] - BOUNDARY_GAP
        if s["end"] > target:
            s["end"] = max(s["start"] + MIN_LEN, target)

    # Roll up paragraph and chapter timings
    for ch in book["chapters"]:
        ch_times = []
        for pa in ch["paragraphs"]:
            sents = [s for s in pa["sentences"] if s.get("start") is not None]
            if sents:
                pa["start"] = min(s["start"] for s in sents)
                pa["end"] = max(s["end"] for s in sents)
                ch_times.append((pa["start"], pa["end"]))
        if ch_times:
            ch["start"] = min(t[0] for t in ch_times)
            ch["end"] = max(t[1] for t in ch_times)


def main():
    book_path = Path(sys.argv[1])
    audio_paths = [Path(p) for p in sys.argv[2:-1]]
    out_path = Path(sys.argv[-1])

    book = json.loads(book_path.read_text())
    audio_jsons = [json.loads(p.read_text()) for p in audio_paths]

    book_tokens, backrefs = flatten_book(book)
    audio_words = flatten_audio(audio_jsons)

    print(f"Book tokens: {len(book_tokens)}", file=sys.stderr)
    print(f"Audio tokens: {len(audio_words)}", file=sys.stderr)

    matches = align(book_tokens, audio_words)
    print(f"Matched pairs: {len(matches)} ({100*len(matches)/max(1,len(audio_words)):.1f}% of audio)", file=sys.stderr)

    collect_timings(book, backrefs, matches)

    # Stats
    matched_sents = 0
    total_sents = 0
    for ch in book["chapters"]:
        for pa in ch["paragraphs"]:
            for s in pa["sentences"]:
                total_sents += 1
                if s.get("match_ratio", 0) > 0.3:
                    matched_sents += 1
    print(f"Sentences with good alignment: {matched_sents}/{total_sents}", file=sys.stderr)

    out_path.write_text(json.dumps(book, ensure_ascii=False))
    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
