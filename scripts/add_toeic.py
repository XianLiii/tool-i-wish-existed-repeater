#!/usr/bin/env python3
"""
Add the TOEIC listening book: 14 CD MP3s → concat into one file,
run Whisper, split into 14 chapters by CD boundaries.

Runs end-to-end. Resumable: re-running skips steps whose output already
exists, so if Whisper dies halfway you can just restart without re-doing
the 10-min concat.

Usage:
    python3 scripts/add_toeic.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).parent.parent
SLUG = "toeic-listening"
TITLE = "新托业听力详解及实战试题"
AUTHOR = ""
SOURCE_DIR = Path("/Users/xian/NAS/personal_folder/工作区/2026-/托业资料/新托业听力详解及实战试题MP3（part1-4）")

WEB = PROJECT / "web"
BOOK_DIR = WEB / "library" / SLUG
AUDIO_DST = WEB / "audio" / f"{SLUG}.mp3"
WORK = PROJECT / "data" / SLUG
MODELS_DIR = Path.home() / "models" / "whisper"
MODEL_PREFERENCE = [
    "ggml-large-v3-turbo.bin",
    "ggml-large-v3.bin",
    "ggml-medium.en.bin",
    "ggml-small.en.bin",
    "ggml-base.en.bin",
    "ggml-tiny.en.bin",
]
SPECIAL_TOKEN = re.compile(r"\[_[A-Z_]+[0-9]*_*\]")
PARAGRAPH_GAP = 1.2
BOUNDARY_GAP = 0.12
MIN_SENT_LEN = 0.2


def step(msg):
    print(f"\n>>> {msg}", flush=True)


def get_duration(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def pick_model() -> str:
    for name in MODEL_PREFERENCE:
        p = MODELS_DIR / name
        if p.exists():
            return str(p)
    raise SystemExit(f"No whisper model found in {MODELS_DIR}")


def concat_mp3s(cds, out_path: Path, work: Path):
    list_file = work / "concat.txt"
    # ffmpeg concat demuxer wants paths relative to the list file location,
    # or absolute paths with "file '...'" syntax.
    list_file.write_text("\n".join(f"file '{cd}'" for cd in cds) + "\n")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out_path),
        ],
        check=True,
    )


def convert_to_wav(mp3: Path, wav: Path):
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(mp3),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(wav),
        ],
        check=True,
    )


def run_whisper(wav: Path, out_prefix: Path, model: str):
    cmd = [
        "whisper-cli", "-m", model,
        "-f", str(wav),
        "-ojf",                          # JSON full (segments with offsets)
        "-of", str(out_prefix),          # output path base (no extension)
        "-t", "8",                       # threads
        "-l", "en",
    ]
    print(f"  > {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def build_manifest(raw: dict, boundaries: list, audio_rel: str) -> dict:
    """Split Whisper segments into N chapters by boundary time, group
    into paragraphs by silence gap, emit nested manifest."""
    all_sents = []
    for seg in raw.get("transcription", []):
        text = (seg.get("text") or "").strip()
        text = SPECIAL_TOKEN.sub("", text).strip()
        if not text:
            continue
        off = seg.get("offsets", {})
        s = float(off.get("from", 0)) / 1000.0
        e = float(off.get("to", s)) / 1000.0
        if e <= s:
            continue
        all_sents.append({"text": text, "start": s, "end": e})

    # Trim sentence ends to leave a silent micro-gap before next sentence
    for i in range(len(all_sents) - 1):
        cur, nxt = all_sents[i], all_sents[i + 1]
        target = nxt["start"] - BOUNDARY_GAP
        if cur["end"] > target:
            cur["end"] = max(cur["start"] + MIN_SENT_LEN, target)

    chapters = []
    for b in boundaries:
        ch_sents = [s for s in all_sents if b["start"] <= s["start"] < b["end"]]
        if not ch_sents:
            continue
        # Group sentences → paragraphs by pause
        paragraphs = []
        cur, prev_end = [], None
        for s in ch_sents:
            if prev_end is not None and s["start"] - prev_end > PARAGRAPH_GAP:
                if cur:
                    paragraphs.append(cur)
                    cur = []
            cur.append(s)
            prev_end = s["end"]
        if cur:
            paragraphs.append(cur)

        chapter_para = []
        for pi, psents in enumerate(paragraphs):
            pid = f"{b['idx']}.{pi}"
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
        chapters.append({
            "id": b["idx"],
            "title": b["title"],
            "start": b["start"],
            "end": b["end"],
            "paragraphs": chapter_para,
        })

    return {
        "id": SLUG,
        "title": TITLE,
        "author": AUTHOR,
        "audio": audio_rel,
        "chapters": chapters,
    }


def update_library(lib_path: Path):
    data = {"books": []}
    if lib_path.exists():
        data = json.loads(lib_path.read_text())
    books = [b for b in data.get("books", []) if b.get("id") != SLUG]
    entry = {
        "id": SLUG,
        "title": TITLE,
        "manifest": f"library/{SLUG}/manifest.json",
    }
    if AUTHOR:
        entry["author"] = AUTHOR
    books.append(entry)
    data["books"] = books
    lib_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    BOOK_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DST.parent.mkdir(parents=True, exist_ok=True)

    step("Finding CDs")
    cds = sorted(
        SOURCE_DIR.glob("cd*.mp3"),
        key=lambda p: int(re.match(r"cd(\d+)", p.stem).group(1)),
    )
    if not cds:
        sys.exit(f"No cd*.mp3 files found in {SOURCE_DIR}")
    print(f"  {len(cds)} CDs: {[c.name for c in cds]}")

    # Boundaries (cumulative offsets of each CD in the concatenated file)
    boundaries_path = WORK / "boundaries.json"
    if boundaries_path.exists():
        boundaries = json.loads(boundaries_path.read_text())
        print(f"  Using cached boundaries from {boundaries_path}")
    else:
        step("Measuring durations")
        boundaries = []
        offset = 0.0
        for i, cd in enumerate(cds, 1):
            d = get_duration(cd)
            boundaries.append({
                "idx": i,
                "title": f"CD {i}",
                "start": offset,
                "end": offset + d,
            })
            offset += d
            print(f"  {cd.name}: {d:>8.1f}s  (total so far: {offset/60:>5.1f} min)")
        total = offset
        print(f"  Total: {total:.0f}s ({total/3600:.2f} hours)")
        boundaries_path.write_text(json.dumps(boundaries, ensure_ascii=False, indent=2))

    # Concat MP3s
    if AUDIO_DST.exists() and AUDIO_DST.stat().st_size > 100_000_000:
        print(f"  Concat target exists: {AUDIO_DST} ({AUDIO_DST.stat().st_size/1024/1024:.0f} MB), skipping")
    else:
        step(f"Concatenating {len(cds)} MP3s → {AUDIO_DST}")
        concat_mp3s(cds, AUDIO_DST, WORK)
        print(f"  → {AUDIO_DST.stat().st_size/1024/1024:.0f} MB")

    # WAV conversion
    wav = WORK / "audio.wav"
    if wav.exists():
        print(f"  WAV exists: {wav}, skipping")
    else:
        step("Converting to 16kHz mono WAV for Whisper")
        convert_to_wav(AUDIO_DST, wav)
        print(f"  → {wav.stat().st_size/1024/1024:.0f} MB")

    # Whisper
    whisper_json = WORK / "whisper-out.json"
    if whisper_json.exists():
        print(f"  Whisper output exists: {whisper_json}, skipping transcription")
    else:
        step("Running Whisper (this takes ~2-3 hours on M-series; go have lunch)")
        model = pick_model()
        print(f"  Model: {model}")
        out_prefix = WORK / "whisper-out"
        run_whisper(wav, out_prefix, model)

    raw = json.loads(whisper_json.read_text())

    step("Building 14-chapter manifest")
    manifest = build_manifest(raw, boundaries, audio_rel=f"../../audio/{SLUG}.mp3")
    (BOOK_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    total_sents = sum(len(p["sentences"]) for c in manifest["chapters"] for p in c["paragraphs"])
    total_paras = sum(len(c["paragraphs"]) for c in manifest["chapters"])
    print(f"  Chapters: {len(manifest['chapters'])}, paragraphs: {total_paras}, sentences: {total_sents}")

    step("Updating library.json")
    update_library(WEB / "library.json")
    print(f"  → {WEB / 'library.json'}")

    step("DONE. Reload the web UI.")


if __name__ == "__main__":
    main()
