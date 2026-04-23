# Ingestion pipeline

How Repeater turns an MP3 + a book into a `manifest.json` with ≤50ms per-word timing.

## The problem

You have an audiobook (14 hours, ~5000 sentences) and the book text. You need to know: **where in the audio does sentence N begin and end?**

This is "forced alignment" — a solved problem in the ASR literature, but the naive approach (just use Whisper's timestamps) fails in several ways. Repeater's pipeline stacks three techniques to get a clean result.

## Pipeline overview

```
audio.mp3                                  book.md
   │                                          │
   │  (parse_text.py)                         │
   │                                          ▼
   │                                       book.json   ─┐
   ▼                                                    │
(whisper-cli)                                           │
   │                                                    │
   ▼                                                    │
audio.json  ───────── (align.py) ───────────────────────┤
                                                        │
                      (force_align.py) ◀────────────────┘
                              │
                              ▼
                        manifest.json
```

Three passes, each refining the timing.

## Pass 1: whisper.cpp transcription

`transcribe_wcpp.py` runs whisper.cpp on the full audio and extracts word-level timestamps. The model defaults to the best locally-available: `large-v3-turbo` > `large-v3` > `medium.en` > `small.en` > `base.en` > `tiny.en`.

**Why not just use Whisper's transcription directly?**
- Whisper mistranscribes proper nouns, especially non-English ones. "Úrsula Iguarán" → "Ursula Igwaran" or omitted entirely.
- Homophones are unreliable ("There" / "Their").
- The book's text is the ground truth; the goal is to **align** that text to the audio, not to re-derive it.

Whisper's output is used as **rough coordinates**: approximately when each word is uttered, so we know where in the 50,000-second audio to search.

## Pass 2: fuzzy book ↔ whisper alignment

`align.py` uses Python's `difflib.SequenceMatcher` to find matching blocks between:
- book tokens (from `parse_text.py`)
- Whisper tokens (from `transcribe_wcpp.py`)

For every book sentence, it collects the start/end of the matched Whisper words that fall in that sentence. First-pass timings:

```python
se["start"] = min(word.start for word in matched_words_in_sentence)
se["end"]   = max(word.end for word in matched_words_in_sentence)
```

**Limitation:** ~90% of sentences align, but:
- **Unmatched prefixes** — if Whisper missed "Úrsula Iguarán" at the start of a sentence, `sent.start` points to the first *matched* word ("his" in "Úrsula Iguarán, his wife…"). Playing the sentence misses the proper name entirely.
- **Unmatched suffixes** — same issue at the end. "accent" at the end of `"...proclaimed with a harsh accent."` can get lost.
- **Touching boundaries** — Whisper's `word[i].end` often equals `word[i+1].start` in continuous speech, with no gap. Without compensation, looping bleeds into the next sentence.

## Pass 3: wav2vec2 CTC forced alignment (the fix)

`force_align.py` is the precision layer. For each sentence:

1. Extract a small audio window around the Whisper estimate (±1s padding)
2. Convert the **book text** (ground truth) to uppercase ASCII + `|` separators
3. Run wav2vec2 to get per-frame character probabilities (emission matrix at 50 Hz)
4. Run CTC forced alignment to find the most probable path through the emission matrix that matches the book text
5. Group token spans into words and extract per-word timestamps

```python
bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
emissions = model(waveform)
aligned_tokens, scores = torchaudio.functional.forced_align(emissions, book_tokens, blank=0)
```

Because the book text is **forced** onto the audio (not derived from it), proper nouns and missed words are found correctly. Whisper's contribution here is only providing the ±1s window; the alignment itself uses the book's spelling → phoneme mapping.

Confidence comes from the CTC log-probabilities (converted to `exp(mean_log_prob)`). Sentences below `min-conf` fall back to the Pass-2 timing — guaranteed to be no worse than before.

## Alignment statistics (real books)

From the author's own library:

| Book | Sentences | Pass-2 match | Pass-3 CTC ok | Pass-3 median conf |
|---|---|---|---|---|
| *One Hundred Years of Solitude* | 5555 | 99.9% | 5510 / 5555 (99.2%) | 0.93 |
| *On Writing* | 4073 | 91.7% | 3737 / 4073 (91.8%)  | 0.93 |

Pass-2 match % is high because fuzzy matching of tokens is lenient. Pass-3 is stricter — it either fully aligns the sentence or falls back. The fallback cases are typically audiobook-only content (publisher intros, chapter-number announcements, narrator asides) that don't exist in the book text.

## Edge cases

### Whisper token merging

`transcribe_wcpp.py` detects when Whisper fails to insert a space between a punctuated word and the next one, e.g. `[away.Science]` as a single token. It splits on `./,;:!?"'` + letter boundaries. Without this, entire words can be lost from the Whisper output.

### Overlap clamping

Per-sentence CTC windows overlap by design (the ±1s padding). Adjacent sentences can independently claim overlapping audio. Post-processing clamps each pair so the overlap is split evenly with a 50ms micro-gap — ensures no sentence's audio bleeds into its neighbor's window.

### Loop pause

At playback time, the repeat loop inserts a user-configurable pause (default 500ms) between repetitions. This is independent of alignment — it exists because even a perfectly-aligned loop sounds abrupt without a "breath." See `app.js` → `onTimeUpdate`.

## Known limitations

1. **Audiobook must match the book text closely.** If the audiobook uses a different edition or contains narrator-added content, those passages fall back to Pass-2 timing or become misaligned. `match_ratio` on each sentence surfaces this — sentences below ~0.5 are suspicious.

2. **English only in the forced aligner.** wav2vec2-base-960h is English. For other languages, swap to the appropriate model (`WAV2VEC2_ASR_LARGE_LV60K_960H`, `HUBERT_ASR_LARGE`, multilingual variants like XLS-R).

3. **Long paragraphs hit MPS memory limits.** wav2vec2 attention is quadratic in sequence length, so we chunk at the sentence level (≤30s per window).

4. **Proper nouns unknown to the G2P model** may have imprecise alignments — "Melquíades" works, but obscure place names can be ±100ms off. Usually not audible in practice.

5. **MacBook M-series only, for now.** The pipeline uses PyTorch's MPS backend. CPU works but is 5-10× slower. CUDA is untested.
