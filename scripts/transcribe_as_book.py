"""Transcribe audio and emit a book manifest directly (no source text needed).

Input: <audio.wav> <slug> "<title>" [author] [output_manifest.json]
Output: manifest.json with chapters -> paragraphs -> sentences and timings.

Heuristics:
  - Each Whisper segment becomes one sentence.
  - New paragraph when the gap between sentence end and next start > 1.2s.
  - All content sits inside a single chapter named after the book title.

Use transcribe_wcpp.py first to get audio.json, OR call whisper-cli directly here.
This script takes the audio.json (segments output) produced by a whisper-cpp run
with `-ojf`, not just the flat word list, so we keep sentence grouping.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


MODELS_DIR = Path.home() / "models" / "whisper"
PREFERENCE = [
    "ggml-large-v3-turbo.bin", "ggml-large-v3.bin", "ggml-medium.en.bin",
    "ggml-small.en.bin", "ggml-base.en.bin", "ggml-tiny.en.bin",
]
SPECIAL_TOKEN = re.compile(r"\[_[A-Z_]+[0-9]*_*\]")


def pick_model() -> str:
    for name in PREFERENCE:
        p = MODELS_DIR / name
        if p.exists():
            return str(p)
    raise SystemExit(f"No whisper model found in {MODELS_DIR}")


def run_whisper(audio: str, model: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = str(Path(tmp) / "out")
        cmd = ["whisper-cli", "-m", model, "-f", audio, "-ojf", "-of", prefix, "-t", "8", "-l", "en"]
        print(f"> {' '.join(cmd)}", file=sys.stderr)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            print(proc.stdout, file=sys.stderr)
            raise SystemExit(f"whisper-cli failed (exit {proc.returncode})")
        return json.loads(Path(prefix + ".json").read_text())


def seg_to_sentences(raw: dict, paragraph_gap: float = 1.2):
    """Turn Whisper segments into sentences, grouped into paragraphs by pause."""
    sentences = []
    for seg in raw.get("transcription", []):
        text = (seg.get("text") or "").strip()
        # Strip special whisper tokens that can leak in
        text = SPECIAL_TOKEN.sub("", text).strip()
        if not text:
            continue
        off = seg.get("offsets", {})
        start = float(off.get("from", 0)) / 1000.0
        end = float(off.get("to", start)) / 1000.0
        if end <= start:
            continue
        sentences.append({"text": text, "start": start, "end": end})
    # Group into paragraphs by pause gap
    paragraphs = []
    cur = []
    prev_end = None
    for s in sentences:
        if prev_end is not None and s["start"] - prev_end > paragraph_gap:
            if cur:
                paragraphs.append(cur)
                cur = []
        cur.append(s)
        prev_end = s["end"]
    if cur:
        paragraphs.append(cur)
    return paragraphs


def build_manifest(audio_rel: str, slug: str, title: str, author: str, paragraphs: list):
    # Trim sentence ends to leave a silent gap before the next sentence
    # (see align.py for rationale).
    BOUNDARY_GAP = 0.12
    MIN_LEN = 0.2
    flat = [s for p in paragraphs for s in p]
    for i in range(len(flat) - 1):
        cur, nxt = flat[i], flat[i + 1]
        target = nxt["start"] - BOUNDARY_GAP
        if cur["end"] > target:
            cur["end"] = max(cur["start"] + MIN_LEN, target)

    chapter_para = []
    for pi, psents in enumerate(paragraphs):
        pid = f"1.{pi}"
        chapter_para.append({
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
    chapter = {
        "id": 1,
        "title": title,
        "start": paragraphs[0][0]["start"] if paragraphs else 0,
        "end": paragraphs[-1][-1]["end"] if paragraphs else 0,
        "paragraphs": chapter_para,
    }
    return {
        "id": slug,
        "title": title,
        "author": author,
        "audio": audio_rel,
        "chapters": [chapter],
    }


def main():
    audio = sys.argv[1]
    slug = sys.argv[2]
    title = sys.argv[3]
    author = sys.argv[4] if len(sys.argv) > 4 else ""
    out = Path(sys.argv[5]) if len(sys.argv) > 5 else Path(f"manifest-{slug}.json")
    audio_rel = sys.argv[6] if len(sys.argv) > 6 else f"../../audio/{slug}.mp3"

    model = pick_model()
    print(f"Using model: {model}", file=sys.stderr)
    raw = run_whisper(audio, model)
    paragraphs = seg_to_sentences(raw)
    n_sent = sum(len(p) for p in paragraphs)
    print(f"Parsed: 1 chapter, {len(paragraphs)} paragraphs, {n_sent} sentences", file=sys.stderr)
    manifest = build_manifest(audio_rel, slug, title, author, paragraphs)
    out.write_text(json.dumps(manifest, ensure_ascii=False))
    print(f"Wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
