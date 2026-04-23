# Contributing

Thanks for considering a contribution! This is a small, personal project, so expectations are informal — but a few guidelines help keep things smooth.

## Development setup

```bash
git clone https://github.com/YOUR_USERNAME/repeater.git
cd repeater
uv sync
brew install ffmpeg whisper-cpp       # or platform equivalent
```

Run the dev server:
```bash
python3 scripts/range_server.py
# → http://localhost:8080
```

The frontend has no build step — edit `web/*.{html,css,js}` and refresh. The server reloads `manifest.json` and data files on every request.

## What's easy to contribute

- **Bug fixes** on alignment edge cases, UI quirks, typos
- **Mobile layout** refinements (we test on iPhone SE / iPad)
- **New language support** — the UI and dictionary are English-biased today
- **Documentation** — `docs/` improvements, examples, screencasts
- **Integrations** — e.g. Anki export for vocab, EPUB import, better text parsers

## What's harder (and we'd love help on)

- Replacing the Python ingestion with a cross-platform binary (e.g. Rust + whisper-rs + wav2vec2 in ONNX)
- iOS/Android wrappers (Capacitor or native)
- Cloud alignment service for users without CLI skills
- Multi-user / shared vocab sync

## Code style

- **Python:** standard library first, no frameworks. Keep scripts runnable with `python3 scripts/<name>.py`. No global state. Type hints welcome but not required.
- **JavaScript:** vanilla, no build step, no framework. ES2022+ syntax. Keep `app.js` roughly organized by feature blocks.
- **CSS:** BEM-ish classes, mobile-first media queries at the end of the file. Variables in `:root` for theming.

## Opening an issue

Before filing: check the existing issues and the "Known limitations" section in [docs/INGESTION.md](docs/INGESTION.md).

Good bug reports include:
- A specific sentence/timestamp where the bug shows
- The `match_ratio` and `start/end` from `manifest.json` for that sentence
- The corresponding lines in the Whisper `audio.json` (if available)
- What you expected vs what happened

## Opening a PR

- Branch from `main`
- One logical change per PR — smaller is faster to review
- Update `docs/` if behavior changes
- Screenshots for UI changes, ideally before/after
- Don't commit personal audio/text under `web/audio/` or `web/library/` (the `.gitignore` should prevent this — sanity-check anyway)

## Code of conduct

Be kind. Critique code, not people. Assume good faith.
