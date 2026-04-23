"""Run whisper.cpp (whisper-cli) and extract word-level timestamps.

The previous version used `-ml 1 -sow` which caused whisper to drop content.
Instead, run with default segmentation and aggregate tokens within each segment
into words (a new word starts on a token whose text begins with whitespace).

Output shape is unchanged so align.py works as-is:
  {"audio", "duration", "text", "words":[{"w","start","end"},...]}
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


_MODELS_DIR = Path.home() / "models" / "whisper"
# Prefer the best available local model; fall back if not downloaded yet.
_PREFERENCE = ["ggml-large-v3-turbo.bin", "ggml-large-v3.bin", "ggml-medium.en.bin",
               "ggml-small.en.bin", "ggml-base.en.bin", "ggml-tiny.en.bin"]
DEFAULT_MODEL = next(
    (str(_MODELS_DIR / name) for name in _PREFERENCE if (_MODELS_DIR / name).exists()),
    str(_MODELS_DIR / "ggml-base.en.bin"),
)
SPECIAL_TOKEN = re.compile(r"\[_[A-Z_]+[0-9]*_*\]|\[_[A-Z]+_\]")


def run_whisper(audio: str, model: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "out")
        cmd = [
            "whisper-cli",
            "-m", model,
            "-f", audio,
            "-ojf",
            "-of", prefix,
            "-t", "8",
            "-l", "en",
        ]
        print(f"> {' '.join(cmd)}", file=sys.stderr)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            print(proc.stdout, file=sys.stderr)
            raise SystemExit(f"whisper-cli failed (exit {proc.returncode})")
        return json.loads(Path(prefix + ".json").read_text())


def extract_words(raw: dict, offset: float) -> tuple[list[dict], float, str]:
    """Aggregate whisper.cpp tokens into words using leading-space boundaries."""
    words: list[dict] = []
    text_parts: list[str] = []
    max_end = 0.0
    cur: dict | None = None  # {text, start, end}

    def flush():
        nonlocal cur
        if cur and cur["text"].strip():
            w = cur["text"].strip()
            # Strip surrounding punctuation but keep internal
            w_clean = w.strip(".,;:!?\"'()[]{}-—–…")
            if w_clean:
                words.append({"w": w, "start": cur["start"], "end": cur["end"]})
                text_parts.append(w)
        cur = None

    for seg in raw.get("transcription", []):
        for tok in seg.get("tokens", []):
            text = tok.get("text", "")
            if not text or SPECIAL_TOKEN.match(text):
                continue
            off = tok.get("offsets", {})
            start_ms = float(off.get("from", 0))
            end_ms = float(off.get("to", start_ms))
            if end_ms <= start_ms:
                continue
            start = start_ms / 1000.0 + offset
            end = end_ms / 1000.0 + offset

            starts_word = text.startswith(" ")
            # Whisper sometimes fails to insert a leading space between a
            # punctuated word and the next word (e.g., [away.Science]). Force
            # a split whenever the current token ends in sentence punctuation
            # and the new token begins with a letter.
            prev_char = cur["text"][-1] if cur and cur["text"] else ""
            first_char = text.lstrip()[:1]
            punct_break = prev_char in '.,;:!?"\'' and first_char.isalpha()
            if starts_word or cur is None or punct_break:
                flush()
                cur = {"text": text.lstrip() if punct_break and not starts_word else text,
                       "start": start, "end": end}
            else:
                cur["text"] += text
                cur["end"] = end
            max_end = max(max_end, end)
    flush()
    return words, max_end, " ".join(text_parts).strip()


def main():
    audio = sys.argv[1]
    out = Path(sys.argv[2])
    offset = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    model = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_MODEL

    raw = run_whisper(audio, model)
    words, duration, text = extract_words(raw, offset)
    data = {"audio": audio, "duration": duration, "text": text, "words": words}
    out.write_text(json.dumps(data, ensure_ascii=False))
    print(f"Wrote {out} — {len(words)} words, {duration:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
