"""force_align.py — wav2vec2 CTC forced alignment, replaces align.py for precision.

Takes book text (ground truth) + audio, produces per-word timestamps with
≤50ms precision. Handles:
  - Proper nouns missed by Whisper (Úrsula, Melquíades, etc.)
  - Word boundary bleed (no BOUNDARY_GAP heuristic needed)
  - Whisper token aggregation bugs ([away.Science] type merges)

Usage:
  python force_align.py <book.json> <audio.wav> <output_manifest.json> \\
      [--windows <whisper_audio.json>] [--min-conf 0.3]

Algorithm:
  1. Load wav2vec2 model (char-level CTC emissions at 50Hz = 20ms/frame)
  2. Chunk audio by book paragraph
     - If whisper windows provided: use those ±2s padding
     - Otherwise: uniform 60s chunks
  3. For each chunk:
     a. Normalize book text to ASCII uppercase + '|' separators
     b. Run forced_align() → per-frame token assignment
     c. merge_tokens() → per-token spans with confidence scores
     d. Group tokens by '|' separator → per-word spans
  4. Aggregate into sentence timings (min/max of constituent word timings)
  5. Low-confidence chunks (< min-conf) fall back to Whisper timings if provided
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import soundfile as sf
import torch
import torchaudio

# ---- Model init ----

DEVICE = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
print(f"[force_align] device = {DEVICE}", file=sys.stderr, flush=True)

BUNDLE = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
MODEL = BUNDLE.get_model().to(DEVICE)
MODEL.eval()
LABELS = BUNDLE.get_labels()
DICT = {c: i for i, c in enumerate(LABELS)}
BLANK_ID = 0
WORD_SEP = "|"
WORD_SEP_ID = DICT[WORD_SEP]
SAMPLE_RATE = BUNDLE.sample_rate  # 16000

WORD_RE = re.compile(r"[a-zA-Z\u00c0-\u024f]+(?:'[a-zA-Z]+)?")


def ascii_upper(text: str) -> str:
    """Strip diacritics (Úrsula → URSULA) and return uppercase letters only."""
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    return text.upper()


def tokenize_sentence(text: str) -> list[str]:
    """Book tokens, same rule as align.py."""
    return [m.group(0) for m in WORD_RE.finditer(text)]


def build_ctc_transcript(words: list[str]) -> tuple[str, list[str]]:
    """Join words into a single CTC transcript 'W1|W2|W3' of uppercase A-Z.

    Returns (transcript, kept_words) where kept_words drops empties after ascii-fold.
    """
    out_words = []
    for w in words:
        clean = ascii_upper(w)
        clean = re.sub(r"[^A-Z]", "", clean)  # drop apostrophes, punctuation
        if clean:
            out_words.append(clean)
    return WORD_SEP.join(out_words), out_words


_AUDIO_CACHE: dict = {}

def preload_audio(path: Path) -> torch.Tensor:
    """Load entire wav file into RAM once. Returns (num_samples,) tensor."""
    p = str(path)
    if p in _AUDIO_CACHE:
        return _AUDIO_CACHE[p]
    print(f"[force_align] preloading audio into RAM: {path}", file=sys.stderr, flush=True)
    data, sr = sf.read(p, dtype="float32")
    if sr != SAMPLE_RATE:
        raise ValueError(f"audio must be {SAMPLE_RATE}Hz, got {sr}Hz")
    if data.ndim > 1:
        data = data.mean(axis=1)
    tensor = torch.from_numpy(data)
    size_mb = tensor.numel() * 4 / 1_000_000
    print(f"[force_align] loaded {size_mb:.1f} MB ({data.shape[0] / SAMPLE_RATE:.1f}s)",
          file=sys.stderr, flush=True)
    _AUDIO_CACHE[p] = tensor
    return tensor


def load_audio(path: Path, start: float = 0.0, duration: float | None = None) -> torch.Tensor:
    """Return a slice of the preloaded waveform as (1, num_samples)."""
    full = preload_audio(path)
    start_idx = max(0, int(start * SAMPLE_RATE))
    if duration is None:
        slice_ = full[start_idx:]
    else:
        end_idx = min(full.numel(), start_idx + int(duration * SAMPLE_RATE))
        slice_ = full[start_idx:end_idx]
    return slice_.unsqueeze(0)


def align_chunk(waveform: torch.Tensor, book_words: list[str]):
    """Align a chunk of audio to a list of book words.

    Returns list of (word, start_sec_rel, end_sec_rel, score) or None on failure.
    The times are relative to the start of `waveform`.
    """
    if waveform.size(1) < SAMPLE_RATE * 0.1:  # <100ms
        return None
    transcript, clean_words = build_ctc_transcript(book_words)
    if not transcript:
        return []

    # Emissions
    with torch.inference_mode():
        emissions, _ = MODEL(waveform.to(DEVICE))
        emissions = torch.log_softmax(emissions, dim=-1)
    emission = emissions[0].cpu()
    num_frames = emission.size(0)

    tokens = [DICT[c] for c in transcript if c in DICT]
    if not tokens or len(tokens) > num_frames:
        return None

    try:
        targets = torch.tensor([tokens], dtype=torch.int32)
        input_lengths = torch.tensor([num_frames])
        target_lengths = torch.tensor([len(tokens)])
        aligned, scores = torchaudio.functional.forced_align(
            emission.unsqueeze(0), targets, input_lengths, target_lengths, blank=BLANK_ID
        )
    except Exception as e:
        print(f"  forced_align error: {e}", file=sys.stderr, flush=True)
        return None

    aligned_list = aligned[0].tolist()
    scores_list = scores[0].tolist()
    token_spans = torchaudio.functional.merge_tokens(aligned[0], scores[0], blank=BLANK_ID)
    # token_spans: list of TokenSpan(token, start, end, score)

    # Frame → seconds conversion (wav2vec2-base stride = 320 samples → 0.02s/frame)
    frame_time = waveform.size(1) / num_frames / SAMPLE_RATE

    # Group tokens into words by WORD_SEP
    word_spans = []
    cur = []
    for span in token_spans:
        if span.token == WORD_SEP_ID:
            if cur:
                word_spans.append(cur)
                cur = []
        else:
            cur.append(span)
    if cur:
        word_spans.append(cur)

    if len(word_spans) != len(clean_words):
        # Likely the CTC path collapsed something; abort this chunk
        return None

    import math
    result = []
    for w, spans in zip(clean_words, word_spans):
        if not spans:
            continue
        start_frame = min(s.start for s in spans)
        end_frame = max(s.end for s in spans)
        # torchaudio's merge_tokens returns LOG-probabilities. Convert to
        # probability in [0, 1] for a more intuitive confidence measure.
        avg_logprob = sum(s.score for s in spans) / len(spans)
        score = math.exp(avg_logprob)
        result.append((w, start_frame * frame_time, end_frame * frame_time, score))
    return result


def iter_book_sentences(book: dict):
    """Yield (ci, pi, si, sent_dict) for every sentence in book order."""
    for ci, ch in enumerate(book["chapters"]):
        for pi, pa in enumerate(ch["paragraphs"]):
            for si, se in enumerate(pa["sentences"]):
                yield ci, pi, si, se


def align_book(book: dict, audio_path: Path, window_hints: dict | None = None,
               min_conf: float = 0.2, pad: float = 1.5,
               max_chunk_s: float = 30.0):
    """Align each sentence individually against a small audio window around it.

    Sentence-level chunking avoids MPS OOM that paragraph-level caused on
    multi-minute paragraphs (attention is quadratic in sequence length).
    """
    total_dur = sf.info(str(audio_path)).duration
    print(f"[force_align] audio duration: {total_dur:.1f}s", file=sys.stderr, flush=True)

    stats = {"ok": 0, "fallback": 0, "fail": 0, "total": 0}
    conf_hist = []

    sentences = list(iter_book_sentences(book))
    for ci, pi, si, se in sentences:
        stats["total"] += 1
        key = (ci, pi, si)
        hint = window_hints.get(key) if window_hints else None
        # Also try the sentence's own recorded timing as a fallback hint
        if not hint and se.get("start") is not None and se.get("end") is not None:
            hint = (se["start"], se["end"])

        aligned = None
        t0 = None
        if hint is not None:
            sent_dur = hint[1] - hint[0]
            # Skip CTC for sentences that can't fit in a chunk with any padding.
            # Otherwise we'd truncate audio and the CTC tries to match all the
            # text against a short chunk → low-conf-but-still-above-min-conf
            # garbage result, e.g. a 47s sentence clamped to 30s with start/end
            # both wrong. Fallback (Whisper align.py timing) is correct for these.
            if sent_dur >= max_chunk_s - 2 * pad:
                pass  # leave aligned=None → fall through to fallback below
            else:
                t0 = max(0.0, hint[0] - pad)
                t1 = min(total_dur, hint[1] + pad)
                if t1 - t0 > max_chunk_s:
                    # Cap at max_chunk_s centered on the sentence
                    mid = (hint[0] + hint[1]) / 2
                    t0 = max(0.0, mid - max_chunk_s / 2)
                    t1 = min(total_dur, t0 + max_chunk_s)
                words = tokenize_sentence(se["text"])
                if words and t1 > t0:
                    waveform = load_audio(audio_path, start=t0, duration=t1 - t0)
                    aligned = align_chunk(waveform, words)
                    # Free MPS memory after each call
                    if hasattr(torch.mps, "empty_cache"):
                        torch.mps.empty_cache()

        if aligned and len(aligned) > 0:
            mean_conf = sum(a[3] for a in aligned) / len(aligned)
            conf_hist.append(mean_conf)
            if mean_conf >= min_conf:
                se["start"] = round(t0 + aligned[0][1], 3)
                se["end"] = round(t0 + aligned[-1][2], 3)
                se["match_ratio"] = round(mean_conf, 3)
                stats["ok"] += 1
                print(f"  [{stats['total']}/{len(sentences)}] ch{ci} pa{pi} se{si}: "
                      f"conf={mean_conf:.2f} {se['start']:.2f}→{se['end']:.2f}",
                      file=sys.stderr, flush=True)
                continue

        stats["fallback" if hint else "fail"] += 1

    # Post-process: per-sentence CTC windows overlap by design (padding),
    # so adjacent sentences can claim overlapping audio. Clamp each pair so
    # no overlap — split the overlap at the midpoint.
    flat = [se for _, _, _, se in sentences]
    for i in range(len(flat) - 1):
        cur, nxt = flat[i], flat[i + 1]
        if (cur.get("end") is None or nxt.get("start") is None
                or cur.get("start") is None):
            continue
        if cur["end"] > nxt["start"]:
            mid = (cur["end"] + nxt["start"]) / 2
            # Leave a 50 ms micro-gap so repeat-loop doesn't bleed
            cur["end"] = round(mid - 0.025, 3)
            nxt["start"] = round(mid + 0.025, 3)
        # Also ensure minimum sentence duration 0.2s
        if cur["end"] - cur["start"] < 0.2:
            cur["end"] = round(cur["start"] + 0.2, 3)

    # Roll up paragraph / chapter timings from sentence timings
    for ch in book["chapters"]:
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

    if conf_hist:
        conf_hist.sort()
        med = conf_hist[len(conf_hist) // 2]
        p10 = conf_hist[len(conf_hist) // 10]
        print(f"[force_align] confidence: median={med:.2f} p10={p10:.2f}", file=sys.stderr, flush=True)
    print(f"[force_align] ok={stats['ok']} fallback={stats['fallback']} "
          f"fail={stats['fail']} total={stats['total']}", file=sys.stderr, flush=True)


def load_window_hints(manifest_path: Path) -> dict:
    """Read an existing manifest to get (ci,pi,si) → (start,end) sentence hints."""
    data = json.loads(manifest_path.read_text())
    hints = {}
    for ci, ch in enumerate(data["chapters"]):
        for pi, pa in enumerate(ch["paragraphs"]):
            for si, se in enumerate(pa["sentences"]):
                if se.get("start") is not None and se.get("end") is not None:
                    hints[(ci, pi, si)] = (se["start"], se["end"])
    return hints


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book_json", help="book.json or existing manifest.json")
    ap.add_argument("audio_wav")
    ap.add_argument("output_manifest")
    ap.add_argument("--hints", help="existing manifest.json to use as paragraph windows")
    ap.add_argument("--min-conf", type=float, default=0.2)
    ap.add_argument("--pad", type=float, default=1.0, help="audio window padding (seconds)")
    args = ap.parse_args()

    book = json.loads(Path(args.book_json).read_text())
    hints = load_window_hints(Path(args.hints)) if args.hints else None

    align_book(book, Path(args.audio_wav), window_hints=hints,
               min_conf=args.min_conf, pad=args.pad)

    Path(args.output_manifest).write_text(json.dumps(book, ensure_ascii=False))
    print(f"[force_align] wrote {args.output_manifest}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
