#!/usr/bin/env python3
"""Render the static site from data/papers.json + templates/index.html.

    python scripts/generate_site.py                    # -> dist/
    python scripts/generate_site.py --out public --serve

Output is fully self-contained: index.html, style.css, app.js, papers.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
except ImportError:  # pragma: no cover - dependency guidance
    print("Jinja2 is required. Install it with:  pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "templates"
ASSET_DIR = REPO_ROOT / "site"
DEFAULT_DATA = REPO_ROOT / "data" / "papers.json"
DEFAULT_OUT = REPO_ROOT / "dist"

ASSETS = ("style.css", "app.js")

# Inline SVG favicon: the Siri-gradient swipe mark.
FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='%238b5cf6'/><stop offset='.5' stop-color='%232f8bff'/>"
    "<stop offset='1' stop-color='%2322d3ee'/></linearGradient></defs>"
    "<rect width='64' height='64' rx='16' fill='%230a0a0f'/>"
    "<rect x='14' y='12' width='30' height='40' rx='7' fill='none' stroke='url(%23g)' stroke-width='3.5' "
    "transform='rotate(-10 29 32)'/>"
    "<rect x='24' y='14' width='30' height='40' rx='7' fill='url(%23g)' opacity='.9' "
    "transform='rotate(8 39 34)'/></svg>"
)


def load_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        print(
            f"{path} not found. Run scripts/fetch_papers.py first (or pass --data).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("papers", [])
    data.setdefault("tags", [])
    data.setdefault("count", len(data["papers"]))
    data.setdefault("date", "")
    data.setdefault("date_label", data["date"])
    data.setdefault("date_short", data["date"])
    data.setdefault("generated_at", "")
    data.setdefault("source_url", "https://huggingface.co/papers")
    return data


def asset_version(paths: list[Path], data_path: Path) -> str:
    """Short content hash so GitHub Pages' CDN can't serve a stale css/js pair."""
    digest = hashlib.sha256()
    for path in [*paths, data_path]:
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def build(data_path: Path, out_dir: Path) -> Path:
    data = load_data(data_path)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("index.html")

    # Embedded raw inside <script type="application/json">, where the browser does
    # NOT decode HTML entities — so it must be emitted with `| safe` and made
    # script-safe here instead. Escaping < > & as \uXXXX keeps it valid JSON while
    # making "</script>", "<!--" and "<script" unrepresentable in the output.
    data_json = (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )

    html = template.render(
        data=data,
        data_json=data_json,
        favicon=urllib.parse.quote(FAVICON_SVG, safe="'%<>=/.: -"),
        asset_version=asset_version([ASSET_DIR / name for name in ASSETS], data_path),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    for name in ASSETS:
        shutil.copyfile(ASSET_DIR / name, out_dir / name)

    # Ship the data too: handy for debugging and for anyone wanting the raw feed.
    shutil.copyfile(data_path, out_dir / "papers.json")

    # Tell GitHub Pages not to run Jekyll over the output.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    size_kb = (out_dir / "index.html").stat().st_size / 1024
    print(f"Built {out_dir}/index.html — {data['count']} papers, {size_kb:.0f} KB")
    return out_dir


def serve(directory: Path, port: int) -> None:
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Serving {directory} at http://localhost:{port}  (ctrl-c to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the PaperSwipe static site")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="Path to papers.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output directory")
    parser.add_argument("--serve", action="store_true", help="Serve the output after building")
    parser.add_argument("--port", type=int, default=8000, help="Port for --serve")
    args = parser.parse_args()

    out_dir = build(Path(args.data), Path(args.out))
    if args.serve:
        serve(out_dir, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
