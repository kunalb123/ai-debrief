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
DEFAULT_NEWS = REPO_ROOT / "data" / "news.json"
DEFAULT_OUT = REPO_ROOT / "dist"

ASSETS = ("style.css", "app.js")

# Inline SVG favicon: the Siri-gradient swipe mark.
FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
    "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
    "<stop offset='0' stop-color='%23ffffff'/><stop offset='1' stop-color='%239a9ba0'/>"
    "</linearGradient></defs>"
    "<rect width='64' height='64' rx='16' fill='%23000000'/>"
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
    data.setdefault("date_start", data["date"])
    data.setdefault("date_end", data["date"])
    data.setdefault("date_label", data["date"])
    data.setdefault("date_short", data["date"])
    data.setdefault("days", 1)
    data.setdefault("generated_at", "")
    data.setdefault("source_url", "https://huggingface.co/papers")
    return data


def load_news(path: Path) -> dict[str, Any]:
    """News is optional: a missing or unreadable news.json degrades to papers only."""
    if not path.exists():
        return {"news": [], "tags": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{path} is not valid JSON ({exc}); building without news.", file=sys.stderr)
        return {"news": [], "tags": []}
    data.setdefault("news", [])
    data.setdefault("tags", [])
    return data


def asset_version(paths: list[Path], data_path: Path) -> str:
    """Short content hash so GitHub Pages' CDN can't serve a stale css/js pair."""
    digest = hashlib.sha256()
    for path in [*paths, data_path]:
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def interleave(papers: list[dict[str, Any]], news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blend the two feeds without inventing a score that spans them.

    A paper's upvotes and a story's outlet count measure different things and
    are not comparable, so rather than normalise them into a single fake number
    we keep each list in its own ranking and draw from whichever is further
    behind its share of the output. The result stays proportional the whole way
    down: no clump of news at the top, no tail of nothing but papers.
    """
    if not papers or not news:
        return [*papers, *news]

    merged: list[dict[str, Any]] = []
    p = n = 0
    while p < len(papers) or n < len(news):
        if n >= len(news) or (p < len(papers) and p / len(papers) <= n / len(news)):
            merged.append(papers[p])
            p += 1
        else:
            merged.append(news[n])
            n += 1
    return merged


def merge_feeds(data: dict[str, Any], news_data: dict[str, Any], content: str) -> None:
    """Fold papers and news into one `items` list, tagged by kind.

    Everything downstream — the template, app.js, the filter chips — reads
    `cards`, so the three content modes differ only in what lands here.
    """
    papers = data.get("papers", []) if content in ("mixed", "papers") else []
    news = news_data.get("news", []) if content in ("mixed", "news") else []

    for paper in papers:
        paper["kind"] = "paper"
    for story in news:
        story["kind"] = "news"

    items = interleave(papers, news) if content == "mixed" else [*papers, *news]
    for position, item in enumerate(items, start=1):
        item["position"] = position

    # Tags stay grouped by kind rather than pooled and re-sorted, so the filter
    # row reads as two families in the same order the cards use them.
    tags: list[dict[str, Any]] = []
    if papers:
        tags += [{**tag, "kind": "paper"} for tag in data.get("tags", [])]
    if news:
        tags += [{**tag, "kind": "news"} for tag in news_data.get("tags", [])]

    # Named `cards`, not `items`: in Jinja `data.items` resolves to the dict's
    # own .items() method before it ever looks for the key.
    data["cards"] = items
    # `cards` holds the same objects, so leaving `papers` in place would embed
    # every abstract in the page twice.
    data.pop("papers", None)
    data["tags"] = tags
    data["content"] = content
    data["paper_count"] = len(papers)
    data["news_count"] = len(news)
    data["count"] = len(items)
    data["news_sources"] = news_data.get("sources", []) if news else []
    # Papers-only builds keep papers.json's own window; a news-only build has to
    # take its labels from news.json or the masthead would date the wrong feed.
    if content == "news" and news_data.get("date_label"):
        for key in ("date", "date_start", "date_end", "date_label", "date_short"):
            if news_data.get(key):
                data[key] = news_data[key]


def build(
    data_path: Path,
    out_dir: Path,
    link_target: str = "pdf",
    news_path: Path | None = None,
    content: str = "mixed",
) -> Path:
    data = load_data(data_path)
    news_data = load_news(news_path) if news_path else {"news": [], "tags": []}
    merge_feeds(data, news_data, content)
    # Lives on `data` rather than beside it, so the embedded JSON carries it too
    # and app.js renders deck cards with the same destination as the template.
    data["link_target"] = link_target

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
    mix = f"{data['paper_count']} papers + {data['news_count']} news"
    print(f"Built {out_dir}/index.html — {data['count']} cards ({mix}), {size_kb:.0f} KB")
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
    parser = argparse.ArgumentParser(description="Generate the paperswipe static site")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="Path to papers.json")
    parser.add_argument("--news", default=str(DEFAULT_NEWS), help="Path to news.json")
    parser.add_argument(
        "--content",
        choices=("mixed", "papers", "news"),
        default="mixed",
        help="Which feeds to include (default: both, interleaved)",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output directory")
    parser.add_argument("--serve", action="store_true", help="Serve the output after building")
    parser.add_argument("--port", type=int, default=8000, help="Port for --serve")
    parser.add_argument(
        "--link-target",
        choices=("pdf", "abstract"),
        default="pdf",
        help="Where the card's Read button points (default: the arXiv PDF)",
    )
    args = parser.parse_args()

    out_dir = build(
        Path(args.data),
        Path(args.out),
        args.link_target,
        news_path=Path(args.news),
        content=args.content,
    )
    if args.serve:
        serve(out_dir, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
