# Architecture

A technical tour of how Repeater is put together.

## Overview

Three layers, each deliberately small:

```
┌────────────────────────────────────────────────────────┐
│  web/    — static frontend, ~1500 LOC vanilla JS       │
│            Plays audio, renders text, tracks user data │
└────────────────────────────────────────────────────────┘
                          │ HTTP (localhost)
┌────────────────────────────────────────────────────────┐
│  range_server.py  — tiny Python runtime server         │
│    · serves static files with HTTP Range (audio seek)  │
│    · POST /data/*.json   → persists user data to disk  │
│    · POST /api/enrich    → proxies to Anthropic API    │
└────────────────────────────────────────────────────────┘
                          │ filesystem
┌────────────────────────────────────────────────────────┐
│  scripts/  — one-shot ingestion pipeline (offline)     │
│    audio.mp3 + book.md → manifest.json                 │
│    (whisper.cpp → align.py → force_align.py)           │
└────────────────────────────────────────────────────────┘
```

The frontend is self-contained: zero build step, zero framework. Editing `app.js` and refreshing the browser is the dev loop.

## Data model

Every book is a single `manifest.json` with nested structure:

```jsonc
{
  "id": "slug",
  "title": "Book Title",
  "author": "Author Name",
  "audio": "../../audio/slug.mp3",
  "chapters": [
    {
      "id": 1,
      "title": "Chapter 1",
      "start": 22.76,     // seconds
      "end": 2134.05,
      "paragraphs": [
        {
          "id": "1.0",
          "start": 22.76,
          "end": 133.78,
          "sentences": [
            {
              "id": "1.0.0",
              "text": "MANY YEARS LATER as he faced the firing squad, …",
              "start": 22.76,
              "end": 34.15,
              "match_ratio": 0.95    // confidence 0–1
            }
          ]
        }
      ]
    }
  ]
}
```

Chapter → paragraph → sentence, each with its own time window. The frontend flattens these into three global indexes (`state.sentences`, `state.paragraphs`, `state.chapters`) for fast lookup during playback.

## Playback loop

The core repeat behavior lives in one function (`onTimeUpdate` in `app.js`):

```js
if (t >= ref.end - tolerance) {
  if (stillRepeating) {
    audio.pause();
    setTimeout(() => {
      audio.currentTime = ref.start;
      audio.play();
    }, getPauseMs());   // user-configurable "breathing" gap
  } else {
    advanceUnit();      // move to next sentence/paragraph/chapter
  }
}
```

`ref` is the current repeat unit — whatever the user selected. Changing unit (sentence ↔ paragraph ↔ chapter) re-anchors `ref` to the container that holds the currently-playing sentence.

The **loop pause** (0.5s default) exists because audio looping with zero gap sounds abrupt. With a pause, the listener perceives a clean "restart" instead of a cut.

## Focus mode

During playback, a CSS gradient fades everything except the current repeat unit:

```css
.reader.focus-mode .sentence[data-dist="0"] { opacity: 1; }
.reader.focus-mode .sentence[data-dist="1"] { opacity: 0.45; }
.reader.focus-mode .sentence[data-dist="2"] { opacity: 0.25; }
.reader.focus-mode .sentence                { opacity: 0.15; }
```

`data-dist` is computed per sentence on every `timeupdate` — distance from the current repeat range. The gradient makes your eye snap to what's playing without highlighting it distractingly.

## Persistence

User data is kept in JSON files under `web/data/`:

- `progress.json` — last-played position per book
- `vocab.json` — saved words with phonetic + translation + mnemonic
- `notes.json` — sentence-level notes
- `stats.json` — per-chapter repeats + reading time

The client calls `fetch('/data/vocab.json', { method: 'POST', body: ... })` to persist. The server writes atomically (see `range_server.py`). This is simpler than a database and works across multiple browser sessions on the same machine.

For cross-device sync, point the server at a shared directory (iCloud Drive, Syncthing, etc.).

## Why no build step / framework

Because the logic is small (~1500 LOC), and we want the project to be forkable with zero setup. Reading `app.js` top-to-bottom is the "docs."

Consequences:
- No TypeScript → runtime errors are caught by testing in the browser
- No bundler → lazy-loading isn't free (but also isn't needed — everything fits under 100KB)
- No framework → state is manual (a `state` object + functions that read/mutate it)

If the codebase grows past ~3000 LOC this may need to change. It hasn't yet.

## Why Python for ingestion

The alignment pipeline uses PyTorch (wav2vec2) and whisper.cpp, both of which have mature Python bindings. Porting to Rust/Go is feasible but would double the surface area.

The only part that touches Python at runtime is `range_server.py`, which is a ~200-line stdlib-only HTTP server. No web framework, no dependencies.

## Mobile layout

Triggered by `@media (max-width: 640px)`:
- Top bar shows only the book title
- `.topbar-actions` are moved via JS into a bottom toolbar
- Panels (TOC, Vocab, Notes, Stats) become full-screen overlays
- Player stacks into three rows (progress / controls / repeat)

JS handles the topbar ↔ bottombar DOM reshuffle on viewport change (`matchMedia.addEventListener('change', ...)`). Same event listeners, same button instances — no duplication.

## Dependencies

Runtime (what ships):
- Python stdlib only (`range_server.py`)
- Browser (any modern)

Ingestion (one-time per book):
- `whisper-cli` binary (brew-installable)
- PyTorch + torchaudio (wav2vec2 model auto-downloaded on first use, ~360MB)
- `soundfile` for WAV I/O
- `eng-to-ipa` for offline phonetic fallback

Optional:
- Anthropic API key for Chinese/mnemonic enrichment
