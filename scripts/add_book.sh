#!/bin/bash
# add_book.sh — register a new book with the repeater.
#
# Usage:
#   bash scripts/add_book.sh <slug> "<title>" <audio.mp3> <text.md> [author]
#   bash scripts/add_book.sh <slug> "<title>" <audio.mp3> -        [author]   # audio-only
#
# Steps (with text):
#   1. Copy audio -> web/audio/<slug>.mp3
#   2. Parse markdown into book structure
#   3. 16kHz WAV
#   4. Transcribe (largest installed whisper.cpp model)
#   5. Align whisper word timings to book sentences
#   6. Emit manifest.json
#   7. Update library.json
#
# Steps (audio-only):
#   1. Copy audio
#   2. 16kHz WAV
#   3. Transcribe
#   4. Build manifest directly from whisper output (single chapter; paragraphs by long pauses)
#   5. Update library.json

set -e

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <slug> \"<title>\" <audio.mp3> <text.md|-> [author]" >&2
  exit 1
fi

SLUG="$1"
TITLE="$2"
AUDIO_SRC="$3"
TEXT_SRC="$4"
AUTHOR="${5:-}"
AUDIO_ONLY=false
if [ "$TEXT_SRC" = "-" ] || [ -z "$TEXT_SRC" ]; then
  AUDIO_ONLY=true
fi

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="$HOME/models/whisper/ggml-base.en.bin"

WEB="$PROJECT/web"
BOOK_DIR="$WEB/library/$SLUG"
AUDIO_DST="$WEB/audio/$SLUG.mp3"
WORK="$PROJECT/data/$SLUG"

mkdir -p "$BOOK_DIR" "$WEB/audio" "$WORK"

echo ">>> Copying audio to $AUDIO_DST"
if [ ! -f "$AUDIO_DST" ] || [ "$AUDIO_SRC" -nt "$AUDIO_DST" ]; then
  cp "$AUDIO_SRC" "$AUDIO_DST"
fi

echo ">>> Converting audio to 16kHz WAV ${CHUNK_SECONDS:+(first ${CHUNK_SECONDS}s only)}"
FFMPEG_TRIM=""
if [ -n "$CHUNK_SECONDS" ]; then FFMPEG_TRIM="-t $CHUNK_SECONDS"; fi
ffmpeg -y -i "$AUDIO_DST" $FFMPEG_TRIM -ar 16000 -ac 1 -c:a pcm_s16le "$WORK/audio.wav" 2>&1 | tail -1

if [ "$AUDIO_ONLY" = "true" ]; then
  echo ">>> Audio-only mode: transcribing + grouping paragraphs from Whisper output"
  python3 "$PROJECT/scripts/transcribe_as_book.py" \
    "$WORK/audio.wav" "$SLUG" "$TITLE" "$AUTHOR" "$BOOK_DIR/manifest.json" "../../audio/$SLUG.mp3"
else
  echo ">>> Parsing text"
  python3 "$PROJECT/scripts/parse_text.py" "$TEXT_SRC" "$WORK/book.json"

  echo ">>> Transcribing with whisper.cpp (for rough paragraph windows)"
  python3 "$PROJECT/scripts/transcribe_wcpp.py" "$WORK/audio.wav" "$WORK/audio.json" 0

  echo ">>> Coarse Whisper→book alignment (used as window hints)"
  python3 "$PROJECT/scripts/align.py" "$WORK/book.json" "$WORK/audio.json" "$WORK/manifest_coarse.json"

  echo ">>> wav2vec2 CTC forced alignment (precision pass, ≤50ms per word)"
  uv run python "$PROJECT/scripts/force_align.py" \
    "$WORK/manifest_coarse.json" "$WORK/audio.wav" "$BOOK_DIR/manifest.json.tmp" \
    --hints "$WORK/manifest_coarse.json" --min-conf 0.3 --pad 1.0

  python3 - <<PY
import json, pathlib
p = pathlib.Path("$BOOK_DIR/manifest.json.tmp")
m = json.loads(p.read_text())
m["id"] = "$SLUG"
m["title"] = "$TITLE"
m["audio"] = "../../audio/$SLUG.mp3"
pathlib.Path("$BOOK_DIR/manifest.json").write_text(json.dumps(m, ensure_ascii=False))
p.unlink()
PY
fi

echo ">>> Updating library.json"
python3 - <<PY
import json, pathlib
lib_path = pathlib.Path("$WEB/library.json")
data = {"books": []}
if lib_path.exists():
    data = json.loads(lib_path.read_text())
books = data.get("books", [])
# Replace existing entry with same id, or append
books = [b for b in books if b.get("id") != "$SLUG"]
entry = {
    "id": "$SLUG",
    "title": "$TITLE",
    "manifest": "library/$SLUG/manifest.json",
}
if "$AUTHOR":
    entry["author"] = "$AUTHOR"
books.append(entry)
data["books"] = books
lib_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
PY

echo ">>> Done. Reload the web UI and $TITLE will appear in the title menu."
