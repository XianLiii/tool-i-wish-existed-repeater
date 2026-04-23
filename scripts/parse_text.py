"""Parse the book markdown into chapters -> paragraphs -> sentences.

Output: data/book.json
  {
    "title": "...",
    "chapters": [
      {"id": 1, "title": "Chapter 1", "paragraphs": [
        {"id": "1.0", "sentences": [
          {"id": "1.0.0", "text": "..."},
          ...
        ]},
        ...
      ]},
      ...
    ]
  }
"""

import json
import re
import sys
from pathlib import Path


SENT_END = re.compile(r'(?<=[.!?])(?:"|\u201d|\u2019)?\s+(?=[A-Z"\u201c\u2018\u2014\u00c0-\u024f])')
ABBREVS = {"Mr.", "Mrs.", "Ms.", "Dr.", "Jr.", "Sr.", "St.", "vs.", "etc.", "Prof.", "Gen.", "Col.", "Sgt.", "Capt.", "Lt."}


def split_sentences(paragraph: str) -> list[str]:
    text = paragraph.strip()
    if not text:
        return []
    candidates = SENT_END.split(text)
    merged: list[str] = []
    for s in candidates:
        if merged and any(merged[-1].rstrip().endswith(abbr) for abbr in ABBREVS):
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    return [s.strip() for s in merged if s.strip()]


def parse(md_path: Path) -> dict:
    raw = md_path.read_text(encoding="utf-8")
    lines = raw.split("\n")

    title = "Untitled"
    chapters: list[dict] = []
    current_chapter: dict | None = None
    current_paragraph: list[str] = []

    def flush_paragraph():
        nonlocal current_paragraph
        if current_chapter is None or not current_paragraph:
            current_paragraph = []
            return
        para_text = " ".join(current_paragraph).strip()
        if not para_text:
            current_paragraph = []
            return
        sentences = split_sentences(para_text)
        if not sentences:
            current_paragraph = []
            return
        pid = f"{current_chapter['id']}.{len(current_chapter['paragraphs'])}"
        current_chapter["paragraphs"].append({
            "id": pid,
            "sentences": [
                {"id": f"{pid}.{i}", "text": s}
                for i, s in enumerate(sentences)
            ],
        })
        current_paragraph = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("# ") and title == "Untitled":
            title = stripped[2:].strip()
            continue

        # Any H2 heading starts a new chapter/section.
        m = re.match(r"^##\s+(.+?)\s*$", stripped)
        if m:
            flush_paragraph()
            heading = m.group(1).strip().rstrip("#").strip()
            current_chapter = {
                "id": len(chapters) + 1,
                "title": heading,
                "paragraphs": [],
            }
            chapters.append(current_chapter)
            continue

        if current_chapter is None:
            continue

        if stripped.startswith("---") or stripped.startswith("> ") or stripped.startswith("**Contents"):
            flush_paragraph()
            continue

        # Skip Obsidian audio/image embeds like ![[file.mp3]]
        if re.match(r"^!\[\[[^\]]+\]\]\s*$", stripped):
            flush_paragraph()
            continue

        if not stripped:
            flush_paragraph()
            continue

        # Strip inline wiki-links but keep the display text: [[target|text]] -> text, [[target]] -> target
        line = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", stripped)
        line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line)
        current_paragraph.append(line)

    flush_paragraph()

    n_sent = sum(len(p["sentences"]) for c in chapters for p in c["paragraphs"])
    n_para = sum(len(c["paragraphs"]) for c in chapters)
    print(f"Parsed: {len(chapters)} chapters, {n_para} paragraphs, {n_sent} sentences", file=sys.stderr)

    return {"title": title, "chapters": chapters}


if __name__ == "__main__":
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    data = parse(src)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}", file=sys.stderr)
