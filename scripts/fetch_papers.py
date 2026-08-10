#!/usr/bin/env python3
"""Fetch the day's papers from the HuggingFace Daily Papers API into data/papers.json.

No API key required. If the requested day has no papers (weekends and holidays are
usually empty), we walk backwards day by day until we find one that does.

    python scripts/fetch_papers.py                 # today (UTC), with fallback
    python scripts/fetch_papers.py --date 2026-08-05
    python scripts/fetch_papers.py --out data/papers.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

API_URL = "https://huggingface.co/api/daily_papers"
USER_AGENT = "paperswipe/1.0 (+https://github.com/topics/paperswipe)"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "papers.json"

# How many days back to look before giving up on finding a populated day.
MAX_LOOKBACK_DAYS = 10
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3


# ---------------------------------------------------------------------------
# Topic tagging
# ---------------------------------------------------------------------------
# Ordered most-specific-first. Each pattern is matched against "title + summary";
# title hits are weighted more heavily since the title is what the paper is *about*.
TOPIC_RULES: list[tuple[str, list[str]]] = [
    ("Interpretability", [
        r"interpretab", r"mechanistic", r"activation patch", r"sparse autoencoder",
        r"\bsae\b", r"probing", r"circuit(s)? in", r"feature attribution",
        r"explainab", r"saliency", r"neuron", r"steering vector", r"representation engineering",
    ]),
    ("Alignment & Safety", [
        r"alignment", r"\brlhf\b", r"\bdpo\b", r"preference optimi", r"safety",
        r"jailbreak", r"red[- ]team", r"harmful", r"toxicity", r"guardrail",
        r"refusal", r"constitutional", r"reward hacking", r"deceptive", r"sycophan",
    ]),
    ("Reinforcement Learning", [
        r"reinforcement learning", r"\brl\b", r"policy gradient", r"\bppo\b", r"\bgrpo\b",
        r"q-learning", r"actor[- ]critic", r"reward model", r"bandit", r"markov decision",
        r"self[- ]play", r"exploration", r"offline rl",
    ]),
    ("Reasoning", [
        r"reasoning", r"chain[- ]of[- ]thought", r"\bcot\b", r"math(ematical)? problem",
        r"theorem", r"proof", r"planning", r"test[- ]time (compute|scaling)",
        r"self[- ]consistency", r"deliberat", r"logic(al)? inference",
    ]),
    ("Agents", [
        r"\bagent(s|ic)?\b", r"tool[- ]use", r"tool[- ]calling", r"function calling",
        r"multi[- ]agent", r"computer use", r"web navigat", r"\bgui\b agent", r"workflow automat",
    ]),
    ("Efficiency", [
        r"efficien", r"quantiz", r"distill", r"prun", r"sparsit", r"\bkv[- ]cache\b",
        r"latency", r"throughput", r"speed[- ]?up", r"memory footprint", r"compress",
        r"\blora\b", r"parameter[- ]efficient", r"\bpeft\b", r"low[- ]rank", r"inference cost",
        r"lightweight", r"faster", r"flash attention",
    ]),
    ("Multimodal", [
        r"multi[- ]?modal", r"vision[- ]language", r"\bvlm\b", r"image[- ]text",
        r"audio[- ]visual", r"cross[- ]modal", r"\bclip\b", r"video[- ]language",
    ]),
    ("Generative", [
        r"diffusion", r"text[- ]to[- ]image", r"text[- ]to[- ]video", r"image generat",
        r"video generat", r"flow matching", r"\bgan\b", r"rectified flow", r"synthesis",
        r"3d generat", r"gaussian splat", r"\bnerf\b", r"rendering",
    ]),
    ("Vision", [
        r"segmentation", r"object detection", r"image classification", r"depth estimation",
        r"point cloud", r"visual recognition", r"optical flow", r"\bocr\b", r"pose estimation",
    ]),
    ("Speech & Audio", [
        r"speech", r"\basr\b", r"text[- ]to[- ]speech", r"\btts\b", r"audio", r"music",
        r"voice", r"speaker", r"acoustic",
    ]),
    ("Robotics", [
        r"robot", r"manipulation", r"embodied", r"navigation", r"sim[- ]to[- ]real",
        r"\bvla\b", r"grasp", r"locomotion", r"autonomous driving",
    ]),
    ("Retrieval", [
        r"retrieval", r"\brag\b", r"vector (search|database)", r"embedding model",
        r"re[- ]?ranking", r"knowledge base", r"long[- ]context retriev",
    ]),
    ("Code", [
        r"code generation", r"program synthesis", r"software engineering", r"\bswe[- ]bench\b",
        r"unit test", r"repository[- ]level", r"code llm", r"programming",
    ]),
    ("Architecture", [
        r"architecture", r"transformer", r"attention mechanism", r"state space model",
        r"\bmamba\b", r"mixture[- ]of[- ]experts", r"\bmoe\b", r"tokeniz", r"positional encoding",
        r"context (length|window)", r"recurren", r"convolution",
    ]),
    ("Data & Evals", [
        r"benchmark", r"dataset", r"evaluat", r"\beval\b", r"leaderboard", r"annotat",
        r"data curation", r"synthetic data", r"contamination", r"human study",
    ]),
    ("Training", [
        r"pre[- ]?training", r"post[- ]?training", r"fine[- ]?tun", r"scaling law",
        r"curriculum", r"continual learning", r"self[- ]supervised", r"instruction tuning",
        r"optimizer", r"loss function",
    ]),
]

COMPILED_RULES = [
    (topic, [re.compile(p, re.IGNORECASE) for p in patterns])
    for topic, patterns in TOPIC_RULES
]

MAX_TAGS = 2
FALLBACK_TAG = "Machine Learning"


def infer_tags(title: str, summary: str) -> list[str]:
    """Score each topic against the paper text and return the best one or two."""
    scores: list[tuple[int, int, str]] = []
    for order, (topic, patterns) in enumerate(COMPILED_RULES):
        score = 0
        for pattern in patterns:
            if pattern.search(title):
                score += 3
            if pattern.search(summary):
                score += 1
        if score:
            # Negative order keeps the more specific (earlier) topic on ties.
            scores.append((score, -order, topic))
    if not scores:
        return [FALLBACK_TAG]
    scores.sort(reverse=True)
    best = scores[0][0]
    tags = [scores[0][2]]
    # Additional tags only when they are a genuinely strong signal, not noise.
    tags += [topic for score, _, topic in scores[1:MAX_TAGS] if score >= max(3, best * 0.5)]
    return tags


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
WHITESPACE_RE = re.compile(r"\s+")
# Sentence break: terminator + space + capital/digit/quote, ignoring common abbreviations.
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
ABBREVIATIONS = ("e.g.", "i.e.", "et al.", "Fig.", "Eq.", "vs.", "cf.", "approx.", "Dr.", "Prof.")


def clean_text(value: Any) -> str:
    """Collapse the newline-wrapped abstract text the API returns into a single line."""
    if not value:
        return ""
    return WHITESPACE_RE.sub(" ", str(value)).strip()


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = SENTENCE_RE.split(text)
    merged: list[str] = []
    for part in parts:
        if merged and merged[-1].endswith(ABBREVIATIONS):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return [p.strip() for p in merged if p.strip()]


def split_teaser(summary: str, sentences: int = 2) -> tuple[str, str]:
    """Split the abstract into the hook shown on the card face and the rest.

    Nothing is truncated here — the card clamps the hook visually and reveals the
    remainder on expand, so the full HuggingFace summary is always available.
    """
    parts = split_sentences(summary)
    if not parts:
        return summary, ""
    return " ".join(parts[:sentences]), " ".join(parts[sentences:])


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def month_label(value: date | None) -> str:
    return value.strftime("%b %Y") if value else ""


def author_names(paper: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for author in paper.get("authors") or []:
        if isinstance(author, dict):
            if author.get("hidden"):
                continue
            name = clean_text(author.get("name") or author.get("fullname"))
        else:
            name = clean_text(author)
        if name and name not in names:
            names.append(name)
    return names


def byline(names: list[str]) -> str:
    """`Yang`, `Yang & Li`, `Yang et al.` — enough to recognise a group at a glance."""
    if not names:
        return "Unknown authors"
    surnames = [n.split()[-1] for n in names]
    if len(surnames) == 1:
        return surnames[0]
    if len(surnames) == 2:
        return f"{surnames[0]} & {surnames[1]}"
    return f"{surnames[0]} et al."


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def api_get(params: dict[str, Any]) -> list[dict[str, Any]]:
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, list) else []
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            print(f"  attempt {attempt}/{REQUEST_RETRIES} failed: {exc}", file=sys.stderr)
    raise RuntimeError(f"HuggingFace API request failed: {last_error}")


def fetch_day(day: date, limit: int) -> list[dict[str, Any]]:
    return api_get({"date": day.isoformat(), "limit": limit})


def fetch_with_fallback(start: date, limit: int, lookback: int) -> tuple[date, list[dict[str, Any]]]:
    """Find the most recent day at or before `start` that actually has papers."""
    for offset in range(lookback + 1):
        day = start - timedelta(days=offset)
        print(f"Fetching daily papers for {day.isoformat()} ...")
        items = fetch_day(day, limit)
        if items:
            print(f"  found {len(items)} papers")
            return day, items
        print("  no papers listed for that day")
    print(f"No papers in the last {lookback} days; falling back to the latest feed.", file=sys.stderr)
    items = api_get({"limit": limit})
    latest = parse_date(items[0].get("publishedAt")) if items else None
    return latest or start, items


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def normalize(entry: dict[str, Any], rank: int) -> dict[str, Any] | None:
    paper = entry.get("paper") or {}
    paper_id = clean_text(paper.get("id") or entry.get("id"))
    title = clean_text(paper.get("title") or entry.get("title"))
    if not paper_id or not title:
        return None

    summary = clean_text(paper.get("summary") or entry.get("summary"))
    names = author_names(paper) or author_names(entry)
    published = parse_date(paper.get("publishedAt") or entry.get("publishedAt"))
    submitted = parse_date(paper.get("submittedOnDailyAt") or entry.get("submittedOnDailyAt"))

    upvotes = paper.get("upvotes")
    if not isinstance(upvotes, int):
        upvotes = 0

    comments = entry.get("numComments")
    if not isinstance(comments, int):
        comments = 0

    teaser, teaser_rest = split_teaser(summary)

    return {
        "id": paper_id,
        "rank": rank,
        "title": title,
        "summary": summary,
        "teaser": teaser,
        "teaser_rest": teaser_rest,
        "sentence_count": len(split_sentences(summary)),
        "tags": infer_tags(title, summary),
        "upvotes": upvotes,
        "comments": comments,
        "authors": names,
        "author_count": len(names),
        "byline": byline(names),
        "published": published.isoformat() if published else "",
        "published_label": month_label(published),
        "submitted": submitted.isoformat() if submitted else "",
        "arxiv_url": f"https://arxiv.org/abs/{paper_id}",
        "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
        "hf_url": f"https://huggingface.co/papers/{paper_id}",
        "thumbnail": clean_text(entry.get("thumbnail") or paper.get("thumbnail")),
        "project_page": clean_text(paper.get("projectPage")),
        "github_repo": clean_text(paper.get("githubRepo")),
    }


def dedupe(papers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for paper in papers:
        if paper["id"] in seen:
            continue
        seen.add(paper["id"])
        unique.append(paper)
    return unique


def build_payload(day: date, entries: list[dict[str, Any]]) -> dict[str, Any]:
    papers = [p for p in (normalize(e, i) for i, e in enumerate(entries)) if p]
    papers = dedupe(papers)
    papers.sort(key=lambda p: (-p["upvotes"], p["rank"]))
    for position, paper in enumerate(papers, start=1):
        paper["rank"] = position

    tag_counts: dict[str, int] = {}
    for paper in papers:
        for tag in paper["tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    tags = [
        {"name": name, "count": count}
        for name, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # %-d (no zero padding) is glibc/BSD only; fall back on Windows.
    no_pad = "%#d" if sys.platform == "win32" else "%-d"

    return {
        "date": day.isoformat(),
        "date_label": day.strftime(f"%A, %B {no_pad}, %Y"),
        "date_short": day.strftime(f"%a, %b {no_pad}"),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(papers),
        "total_upvotes": sum(p["upvotes"] for p in papers),
        "tags": tags,
        "source": "HuggingFace Daily Papers",
        "source_url": f"https://huggingface.co/papers/date/{day.isoformat()}",
        "papers": papers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch HuggingFace Daily Papers into papers.json")
    parser.add_argument("--date", help="Day to fetch (YYYY-MM-DD). Defaults to today in UTC.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path.")
    parser.add_argument("--limit", type=int, default=100, help="Max papers to request.")
    parser.add_argument(
        "--lookback",
        type=int,
        default=MAX_LOOKBACK_DAYS,
        help="How many earlier days to try when the target day is empty.",
    )
    parser.add_argument("--no-fallback", action="store_true", help="Fail instead of walking back to an earlier day.")
    args = parser.parse_args()

    if args.date:
        target = parse_date(args.date)
        if target is None:
            parser.error(f"could not parse --date {args.date!r}; expected YYYY-MM-DD")
    else:
        target = datetime.now(timezone.utc).date()

    if args.no_fallback:
        day, entries = target, fetch_day(target, args.limit)
    else:
        day, entries = fetch_with_fallback(target, args.limit, args.lookback)

    if not entries:
        print("No papers found. Leaving any existing papers.json untouched.", file=sys.stderr)
        return 1

    payload = build_payload(day, entries)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {payload['count']} papers for {payload['date']} to {out_path}")
    print("Topics: " + ", ".join(f"{t['name']} ({t['count']})" for t in payload["tags"][:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
