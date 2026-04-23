---
name: Bug report
about: Something is broken or behaves unexpectedly
title: "[bug] "
labels: bug
assignees: ''
---

## What happened

<!-- Describe the bug. Include the book title or sentence where it occurred if relevant. -->

## Expected

<!-- What did you expect instead? -->

## Reproduce

1.
2.
3.

## Environment

- OS:
- Browser:
- Python version:
- Commit / version:

## Alignment details (for alignment bugs)

If the bug is an audio-text misalignment, include:

- The sentence text and its `start` / `end` / `match_ratio` from `web/library/<slug>/manifest.json`
- The surrounding Whisper words from `data/full/<slug>-audio.json` (if available)
- Audio time where the problem is audible

## Screenshot / screen recording

<!-- Optional but very helpful for UI bugs. -->
