"""Static file HTTP server with Range request support (HTTP 206 Partial Content).

Python's stdlib SimpleHTTPRequestHandler does not support ranges, which breaks
<audio> seeking on large files. This minimal handler adds that.
"""

import http.server
import json as _json
import os
import re
import sys
import urllib.request
from http import HTTPStatus


ENRICH_SYSTEM = (
    "You are a bilingual vocabulary tutor helping a Chinese speaker learn English. "
    "Return ONLY a compact JSON object, no prose, no code fences."
)

ENRICH_PROMPT_TMPL = (
    "Word: {word}\n"
    "English definition: {definition}\n"
    "Context sentence: {context}\n\n"
    "Return JSON with these keys:\n"
    '- "cn": Chinese translation appropriate for the context (1-2 concise phrases, under 20 Chinese chars)\n'
    '- "mnemonic": A memory aid in Chinese (under 60 chars). Use etymology (root/prefix), cognates with Chinese concepts, or a vivid visual association. Be specific and memorable.\n'
    "Example: {{\"cn\": \"史前的\", \"mnemonic\": \"pre-(前) + historic(历史的) → 历史之前\"}}"
)

def _send_json(handler, status, payload):
    body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


KEY_FILE_CANDIDATES = [
    os.environ.get("ANTHROPIC_KEY_FILE", ""),
    os.path.expanduser("~/.anthropic_key"),
    os.path.expanduser("~/.config/repeater/anthropic_key"),
]


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    for p in KEY_FILE_CANDIDATES:
        if p and os.path.isfile(p):
            try:
                with open(p) as f:
                    content = f.read()
                m = re.search(r"sk-[a-zA-Z0-9_-]+", content)
                if m:
                    return m.group(0)
            except Exception:
                pass
    return ""


def _call_claude(word: str, definition: str, context: str) -> dict:
    api_key = _load_api_key()
    if not api_key:
        return {"error": "no ANTHROPIC_API_KEY (env or key file)"}
    prompt = ENRICH_PROMPT_TMPL.format(word=word, definition=definition or "(n/a)", context=context or "(n/a)")
    req_body = _json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 256,
        "system": ENRICH_SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=req_body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
    except Exception as e:
        return {"error": f"claude call failed: {e}"}

    try:
        resp = _json.loads(raw)
        text = resp["content"][0]["text"].strip()
        # Strip code fences if the model added them
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        return _json.loads(text)
    except Exception as e:
        return {"error": f"bad claude response: {e}", "raw": raw[:300]}


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        # /api/enrich — bilingual + mnemonic via Claude
        if self.path == "/api/enrich":
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16 * 1024:
                self.send_error(HTTPStatus.BAD_REQUEST, "bad length")
                return
            try:
                body = _json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self.send_error(HTTPStatus.BAD_REQUEST, "not json")
                return
            word = (body.get("word") or "").strip()[:50]
            defn = (body.get("definition") or "").strip()[:500]
            ctx = (body.get("context") or "").strip()[:500]
            if not word:
                _send_json(self, HTTPStatus.BAD_REQUEST, {"error": "word required"})
                return
            result = _call_claude(word, defn, ctx)
            _send_json(self, HTTPStatus.OK, result)
            return

        # PUT-like JSON writes to files in /data/*.json
        if not self.path.startswith("/data/") or not self.path.endswith(".json"):
            self.send_error(HTTPStatus.FORBIDDEN, "only /data/*.json writable")
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 8 * 1024 * 1024:
            self.send_error(HTTPStatus.BAD_REQUEST, "bad length")
            return
        body = self.rfile.read(length)
        try:
            _json.loads(body.decode("utf-8"))
        except Exception:
            self.send_error(HTTPStatus.BAD_REQUEST, "not json")
            return
        target = self.translate_path(self.path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as out:
            out.write(body)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None

        fs = os.fstat(f.fileno())
        size = fs.st_size
        ctype = self.guess_type(path)

        range_hdr = self.headers.get("Range")
        if not range_hdr:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", self.date_time_string(int(fs.st_mtime)))
            self.end_headers()
            return f

        m = re.match(r"bytes=(\d*)-(\d*)", range_hdr)
        if not m:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid Range")
            f.close()
            return None
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "":
            # suffix: last N bytes
            length = int(end_s)
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        if start >= size or end < start:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            f.close()
            return None
        end = min(end, size - 1)
        length = end - start + 1

        f.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(int(fs.st_mtime)))
        self.end_headers()

        # Stream only the requested range
        return _RangedFile(f, length)


class _RangedFile:
    """File-like wrapper that limits reads to `remaining` bytes."""

    def __init__(self, f, length: int):
        self._f = f
        self._remaining = length

    def read(self, n=-1):
        if self._remaining <= 0:
            return b""
        if n == -1 or n > self._remaining:
            n = self._remaining
        data = self._f.read(n)
        self._remaining -= len(data)
        return data

    def close(self):
        self._f.close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    directory = sys.argv[2] if len(sys.argv) > 2 else "."
    bind = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
    os.chdir(directory)
    handler = RangeHandler
    handler.extensions_map[".mjs"] = "text/javascript"
    server = http.server.ThreadingHTTPServer((bind, port), handler)
    print(f"Serving {os.getcwd()} on http://{bind}:{port}")
    server.serve_forever()
