"""Backfill missing `phonetic` fields in web/data/vocab.json.

Uses dictionaryapi.dev (same free API the web app uses) to look up phonetics
for any saved word that doesn't already have one. Saves the file in place.
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

import eng_to_ipa as ipa_g2p

VOCAB = Path(__file__).resolve().parent.parent / "web" / "data" / "vocab.json"


def fetch_api_phonetic(word: str) -> str | None:
    """Try dictionaryapi.dev (has authentic native phonetics for common words)."""
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    entry = data[0]
    phon = entry.get("phonetic")
    if not phon:
        for p in entry.get("phonetics", []):
            if p.get("text"):
                phon = p["text"]
                break
    return phon or None


def g2p_phonetic(word: str) -> str | None:
    """Offline grapheme-to-phoneme via eng_to_ipa (CMU dict + rules).

    Returns None if the CMU dict doesn't know the word (marked with '*').
    """
    out = ipa_g2p.convert(word).strip()
    if out.endswith("*") or out == word:
        return None
    return f"/{out}/"


def get_phonetic(word: str) -> tuple[str | None, str]:
    """Try API first (authentic), fall back to offline G2P. Returns (phon, source)."""
    phon = fetch_api_phonetic(word)
    if phon:
        return phon, "api"
    phon = g2p_phonetic(word)
    if phon:
        return phon, "g2p"
    return None, "—"


def main():
    if not VOCAB.exists():
        print(f"No vocab file at {VOCAB}", file=sys.stderr)
        return 1
    vocab = json.loads(VOCAB.read_text())
    missing = [v for v in vocab if not v.get("phonetic")]
    print(f"Vocab total: {len(vocab)} — missing phonetic: {len(missing)}")
    if not missing:
        return 0

    filled = {"api": 0, "g2p": 0, "—": 0}
    for i, v in enumerate(missing, 1):
        word = v["word"]
        phon, src = get_phonetic(word)
        if phon:
            v["phonetic"] = phon
            filled[src] += 1
            print(f"  [{i}/{len(missing)}] {word}: {phon} ({src})")
        else:
            filled["—"] += 1
            print(f"  [{i}/{len(missing)}] {word}: (not found)")
        time.sleep(0.1)

    VOCAB.write_text(json.dumps(vocab, ensure_ascii=False))
    print(f"Done. api={filled['api']} g2p={filled['g2p']} failed={filled['—']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
