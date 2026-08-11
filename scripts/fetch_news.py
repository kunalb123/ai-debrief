#!/usr/bin/env python3
"""Fetch the last week of AI industry news from public RSS feeds into data/news.json.

No API key required, standard library only. Every source here was checked for
three things before it earned a place in FEEDS: it answers 200 from a datacenter
IP (so GitHub Actions can reach it), it carries a real `<description>` rather
than a bare headline, and it dates its items.

Sourcing is deliberately *editorial* rather than per-company: general AI desks
cover whoever is newsworthy, so no lab can go missing because it happens not to
publish an RSS feed of its own. Anthropic, which has no feed anywhere, still
lands in the top handful of mentioned organisations through coverage alone.

    python scripts/fetch_news.py                  # the 7 days ending today (local time)
    python scripts/fetch_news.py --days 3
    python scripts/fetch_news.py --out data/news.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# split_teaser / clean_text / split_sentences are identical work for an abstract
# and an article summary, so they are shared rather than reimplemented.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_papers import (  # noqa: E402
    NO_PAD,
    clean_text,
    local_now,
    local_today,
    range_labels,
    split_sentences,
    split_teaser,
)

USER_AGENT = "paperswipe/1.0 (+https://github.com/topics/paperswipe)"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "news.json"

DEFAULT_DAYS = 7
REQUEST_TIMEOUT = 25
MAX_FEED_BYTES = 1_200_000
# One prolific desk should not be able to crowd out the rest of the feed.
MAX_PER_SOURCE = 12
# A card shows a two-sentence teaser and an expandable remainder; anything past
# that is weight the reader never sees. Roughly the length of a paper abstract.
MAX_SUMMARY_CHARS = 900
MAX_SUMMARY_SENTENCES = 6


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
# `ai_only` marks a desk that covers nothing but AI, so everything it publishes
# is in scope. The rest are general technology desks worth having for their
# reporting, but they need the relevance filter applied — roughly half of what
# they publish is not about this subject at all.
FEEDS: list[dict[str, Any]] = [
    {"name": "The Decoder", "url": "https://the-decoder.com/feed/", "ai_only": True},
    {"name": "TechCrunch", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "ai_only": True},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "ai_only": True},
    {"name": "Ars Technica", "url": "https://arstechnica.com/ai/feed/", "ai_only": True},
    {"name": "Wired", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "ai_only": True},
    {"name": "AI Business", "url": "https://aibusiness.com/rss.xml", "ai_only": True},
    {"name": "VentureBeat", "url": "https://venturebeat.com/category/ai/feed/", "ai_only": True},
    {"name": "The Guardian", "url": "https://www.theguardian.com/technology/artificialintelligenceai/rss", "ai_only": True},
    {"name": "SiliconANGLE", "url": "https://siliconangle.com/feed/", "ai_only": False},
    {"name": "Techmeme", "url": "https://www.techmeme.com/feed.xml", "ai_only": False},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/", "ai_only": False},
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/technology/news.rss", "ai_only": False},
    {"name": "NYT", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "ai_only": False},
    # Silicon and datacenter coverage, which the AI desks under-report.
    {"name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all", "ai_only": False},
    {"name": "SemiAnalysis", "url": "https://semianalysis.com/feed/", "ai_only": False},
]

# Applied to the general desks only. Broad on purpose: a false positive costs one
# off-topic card, a false negative silently loses a story.
RELEVANCE_RE = re.compile(
    r"\b(AI|A\.I\.|artificial intelligence|machine learning|LLM|LLMs|neural|deep learning|"
    r"chatbot|generative|transformer|inference|GPU|GPUs|chip|chips|silicon|semiconductor|"
    r"datacenter|data cent(?:er|re)|supercomput|foundation model|language model|"
    r"OpenAI|Anthropic|DeepMind|Gemini|Claude|ChatGPT|GPT-\d|Llama|Copilot|Nvidia|"
    r"Mistral|DeepSeek|Qwen|Grok|robotaxi|autonomous)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# News taxonomy
# ---------------------------------------------------------------------------
# Ordered most-specific-first, mirroring TOPIC_RULES in fetch_papers.py. The
# research topics cannot classify this content — nothing in "Interpretability"
# or "Reasoning" describes a chip announcement — so news gets its own set.
# Keep in step with the .pill[data-tag=...] hues in site/style.css.
NEWS_RULES: list[tuple[str, list[str]]] = [
    ("Chips & compute", [
        r"\bgpu\b", r"\btpu\b", r"\bchip(s|set|maker)?\b", r"silicon", r"semiconductor",
        r"\bwafer\b", r"\bfab(s|rication)?\b", r"foundry", r"\btsmc\b", r"\bhbm\b",
        r"data ?cent(er|re)", r"supercomput", r"accelerator", r"\bcuda\b", r"\bnvlink\b",
        r"compute cluster", r"gigawatt", r"\bnm process\b", r"interconnect",
    ]),
    ("Open weights", [
        r"open[- ]weight", r"open[- ]source(d|s)? (model|llm|ai)", r"open model",
        r"apache 2\.0", r"\bmit licen[cs]e", r"weights (are |were )?released",
        r"openly available", r"downloadable model",
    ]),
    ("Model release", [
        r"\b(launch|releas|unveil|introduc|debut|announc|ship)\w*\b.{0,40}\b(model|llm|ai|assistant|version)\b",
        r"\b(model|llm)\b.{0,30}\b(launch|releas|unveil|debut|available now)\w*",
        r"\bnow available\b", r"\bgenerally available\b", r"\bpreview\b.{0,20}\bmodel\b",
        r"\b(gpt|claude|gemini|llama|qwen|mistral|grok|deepseek|kimi)[- ]?[\d.]+\b",
        r"new (flagship|frontier|reasoning|multimodal) model",
    ]),
    ("Policy", [
        r"regulat", r"\bai act\b", r"executive order", r"lawsuit", r"sue[ds]?\b",
        r"court", r"copyright", r"antitrust", r"senate", r"congress", r"parliament",
        r"\bban(s|ned|ning)?\b", r"complian", r"privacy", r"legislat", r"watchdog",
        r"attorney general", r"\bfine[ds]?\b.{0,20}\b(million|billion)\b",
        r"white house", r"administration", r"government", r"national security",
        r"export control", r"sanction", r"geopolit", r"\bpolicy\b", r"oversight",
        r"security (risk|concern)", r"safety (risk|concern|framework)",
        r"scam", r"fraud", r"deepfake", r"misinformation", r"disinformation",
        r"censorship", r"surveillance", r"\bmilitary\b", r"\bdefense\b",
    ]),
    ("Business", [
        r"funding", r"\braise[sd]?\b", r"valuation", r"\bipo\b", r"acqui(re|sition)",
        r"merger", r"revenue", r"layoff", r"\bhire[sd]?\b", r"\bceo\b", r"\bcfo\b",
        r"partnership", r"\bdeal\b", r"invest(ment|or)", r"\$\d+ ?(m|b|million|billion)",
        r"stake", r"startup", r"profit", r"earnings", r"contract",
        r"co[- ]?founder", r"steps? down", r"shake[- ]?up", r"reorg", r"restructur",
        r"executive", r"leadership", r"appoint", r"\bhiring\b", r"\btalent\b",
        r"billionaire", r"fortune", r"philanthrop", r"\bstock\b", r"\bshares\b",
        r"tender offer", r"buyback", r"\bmarket cap\b", r"\bbubble\b",
    ]),
    ("Research results", [
        r"benchmark", r"\bstudy\b", r"researchers", r"\bpaper\b", r"\barxiv\b",
        r"breakthrough", r"outperform", r"state[- ]of[- ]the[- ]art", r"experiment",
        r"scientists", r"discover", r"\bfindings\b",
        r"predict(s|ed|ion|ing)?\b", r"forecast", r"hypothesis", r"theorem",
        r"\bproof\b", r"solve[sd]?\b", r"capabilit", r"\bevaluat", r"\btrial\b",
        r"protein", r"genome", r"drug discovery", r"\bscience\b",
    ]),
    ("Product", [
        r"\bapp\b", r"\bfeature\b", r"\bapi\b", r"subscription", r"\busers\b",
        r"rollout", r"roll(ed|ing) out", r"integrat", r"\bplugin\b", r"interface",
        r"\bbeta\b", r"update", r"redesign", r"\btier\b",
        # Consumer hardware, as distinct from the silicon in "Chips & compute".
        r"\bdevice\b", r"\bspeaker\b", r"wearable", r"headset", r"\bglasses\b",
        r"\bgadget\b", r"\bphone\b", r"\blaptop\b", r"\bhardware\b",
        r"chatbot", r"assistant", r"by default", r"\benable[sd]?\b",
        r"\bbrowser\b", r"\bcopilot\b", r"available to (users|subscribers|everyone)",
    ]),
]

COMPILED_NEWS_RULES = [
    (topic, [re.compile(p, re.IGNORECASE) for p in patterns])
    for topic, patterns in NEWS_RULES
]

MAX_TAGS = 2
FALLBACK_TAG = "Industry"


def infer_news_tags(title: str, summary: str) -> list[str]:
    """Score the news taxonomy against the item, same weighting as infer_tags()."""
    scores: list[tuple[int, int, str]] = []
    for order, (topic, patterns) in enumerate(COMPILED_NEWS_RULES):
        score = 0
        for pattern in patterns:
            if pattern.search(title):
                score += 3
            if pattern.search(summary):
                score += 1
        if score:
            scores.append((score, -order, topic))
    if not scores:
        return [FALLBACK_TAG]
    scores.sort(reverse=True)
    best = scores[0][0]
    tags = [scores[0][2]]
    tags += [topic for score, _, topic in scores[1:MAX_TAGS] if score >= max(3, best * 0.5)]
    return tags


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------
CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
ITEM_SPLIT_RE = re.compile(r"<(?:item|entry)[\s>]")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)
BODY_RE = re.compile(
    r"<(description|summary|content:encoded|content)[^>]*>(.*?)</\1>", re.S
)
DATE_RE = re.compile(r"<(pubDate|published|updated|dc:date)[^>]*>(.*?)</\1>", re.S)
# RSS puts the URL in the element body, Atom in an href attribute.
LINK_RSS_RE = re.compile(r"<link[^>]*>\s*(https?://[^<\s]+)\s*</link>", re.S)
LINK_ATOM_RE = re.compile(r'<link[^>]*\shref=["\'](https?://[^"\']+)["\']', re.S)
GUID_URL_RE = re.compile(r"<guid[^>]*>\s*(https?://[^<\s]+)\s*</guid>", re.S)

# Feed boilerplate that adds nothing to a card.
BOILERPLATE_RE = re.compile(
    r"(The post .{0,120}? appeared first on .{0,60}?\.?$"
    r"|Continue reading\.?\s*$"
    r"|Read (the full story|more)\b.*$"
    r"|\[[.…]{1,3}\]\s*$"
    r"|\bShare this:.*$)",
    re.IGNORECASE | re.S,
)


def strip_markup(raw: str) -> str:
    """CDATA out, tags out, entities decoded — twice, since feeds double-encode."""
    if not raw:
        return ""
    blocks = CDATA_RE.findall(raw)
    text = " ".join(blocks) if blocks else raw
    for _ in range(2):
        text = TAG_RE.sub(" ", text)
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return clean_text(text)


def parse_when(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is None:
        return None
    # A feed that omits its offset is assumed UTC rather than dropped.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def item_link(chunk: str) -> str:
    for pattern in (LINK_RSS_RE, LINK_ATOM_RE, GUID_URL_RE):
        match = pattern.search(chunk)
        if match:
            return html.unescape(match.group(1).strip())
    return ""


def condense(summary: str) -> str:
    """Clip an article body down to something card-sized.

    Some desks (The Verge, MIT Tech Review, Tom's Hardware) put the entire piece
    in <content:encoded> — one podcast transcript arrived at 60 KB. A card only
    ever shows a teaser plus an expandable remainder, so carrying the full text
    into the page would embed tens of thousands of unread characters twice, once
    as markup and once in the JSON payload. Cut on a sentence boundary so the
    remainder still reads as prose, and only fall back to a hard character cut
    when a "sentence" is itself longer than the whole budget.
    """
    if len(summary) <= MAX_SUMMARY_CHARS:
        return summary

    kept: list[str] = []
    used = 0
    for sentence in split_sentences(summary)[:MAX_SUMMARY_SENTENCES]:
        if kept and used + len(sentence) > MAX_SUMMARY_CHARS:
            break
        kept.append(sentence)
        used += len(sentence) + 1

    text = " ".join(kept) if kept else summary
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(",;:")
    return text.rstrip() + ("…" if len(text) < len(summary) else "")


def parse_feed(body: str) -> list[dict[str, Any]]:
    """Pull (title, link, summary, when) out of RSS or Atom.

    Deliberately regex-based rather than ElementTree: a single malformed entity
    anywhere in a feed makes a strict XML parse raise, which would cost us the
    whole source. Here it costs at most the one item.
    """
    items: list[dict[str, Any]] = []
    for chunk in ITEM_SPLIT_RE.split(body)[1:]:
        title_match = TITLE_RE.search(chunk)
        if not title_match:
            continue
        title = strip_markup(title_match.group(1))
        if not title:
            continue

        summary = ""
        for _, raw in BODY_RE.findall(chunk):
            candidate = strip_markup(raw)
            if len(candidate) > len(summary):
                summary = candidate
        summary = condense(clean_text(BOILERPLATE_RE.sub("", summary)))

        date_match = DATE_RE.search(chunk)
        items.append({
            "title": title,
            "link": item_link(chunk),
            "summary": summary,
            "when": parse_when(date_match.group(2)) if date_match else None,
        })
    return items


def fetch_feed(feed: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    request = urllib.request.Request(
        feed["url"], headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(MAX_FEED_BYTES).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # One dead feed must never take the build down with it.
        return feed, [], str(exc)[:60]
    return feed, parse_feed(body), ""


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("""
a an the of to in for on with and or is are as at by from its it this that these those
new how why what when who will has have had been be was were can could would should
says say said report reports according amid over after before into out up down more
most than then there here about against between during
""".split())


def tokens(title: str) -> set[str]:
    return {t for t in TOKEN_RE.findall(title.lower()) if len(t) > 2 and t not in STOPWORDS}


def signatures(titles: list[str]) -> list[set[str]]:
    """Reduce each title to the tokens that are rare across the whole corpus.

    Words every AI headline contains — "ai", "model", "openai" in a busy week —
    carry no evidence that two stories are the same story. Dropping anything
    common leaves the names and nouns that actually identify an event, which is
    what stops "India's IT sector survives AI" from matching "An African vision
    of artificial intelligence" purely on the words they share with everything.
    """
    token_sets = [tokens(t) for t in titles]
    frequency: Counter[str] = Counter()
    for token_set in token_sets:
        frequency.update(token_set)
    ceiling = max(3, len(titles) // 20)
    return [{t for t in token_set if frequency[t] <= ceiling} for token_set in token_sets]


def cluster(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group items covering the same story, so outlet count can rank them."""
    sigs = signatures([item["title"] for item in items])
    clusters: list[list[int]] = []
    cluster_sigs: list[set[str]] = []
    for index, sig in enumerate(sigs):
        placed = False
        if sig:
            for position, existing in enumerate(cluster_sigs):
                shared = sig & existing
                if len(shared) >= 2 and len(shared) / min(len(sig), len(existing)) >= 0.5:
                    clusters[position].append(index)
                    # Keep the intersection: a cluster's identity is what its
                    # members agree on, not whatever the first arrival said.
                    cluster_sigs[position] = shared
                    placed = True
                    break
        if not placed:
            clusters.append([index])
            cluster_sigs.append(sig)
    return [[items[i] for i in group] for group in clusters]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_", "smid")


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PARAMS)
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", urllib.parse.urlencode(query), "")
    )


def normalize(group: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Turn a cluster of coverage into one card.

    The representative is whichever outlet wrote the most usable summary, since
    that is what the card face shows; every outlet in the cluster is still
    credited, and how many there are is the ranking signal.
    """
    lead = max(group, key=lambda i: (len(i["summary"]), i["when"] or datetime.min.replace(tzinfo=timezone.utc)))
    url = canonical_url(lead["link"])
    if not url:
        return None

    title = lead["title"]
    summary = lead["summary"] or title
    teaser, teaser_rest = split_teaser(summary)
    outlets = sorted({item["source"] for item in group})
    when = max((i["when"] for i in group if i["when"]), default=None)

    return {
        "id": "n" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12],
        "kind": "news",
        "title": title,
        "summary": summary,
        "teaser": teaser,
        "teaser_rest": teaser_rest,
        "sentence_count": len(split_sentences(summary)),
        "tags": infer_news_tags(title, summary),
        "outlet": lead["source"],
        "outlets": outlets,
        "outlet_count": len(outlets),
        "url": url,
        "published": when.date().isoformat() if when else "",
        "published_label": when.strftime(f"%b {NO_PAD}") if when else "",
    }


def build_payload(items: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    news: list[dict[str, Any]] = []
    for group in cluster(items):
        card = normalize(group)
        if card is not None:
            news.append(card)

    # A story two desks thought worth covering outranks one only a single desk
    # ran — the closest thing news has to the upvote signal papers carry.
    news.sort(key=lambda n: (-n["outlet_count"], n["published"] or "", n["title"]))
    for position, card in enumerate(news, start=1):
        card["rank"] = position

    tag_counts: dict[str, int] = {}
    for card in news:
        for tag in card["tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    tags = [
        {"name": name, "count": count}
        for name, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    date_label, date_short = range_labels(start, end)
    return {
        "date": end.isoformat(),
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "date_label": date_label,
        "date_short": date_short,
        "days": (end - start).days + 1,
        "generated_at": local_now().replace(microsecond=0).isoformat(),
        "count": len(news),
        "tags": tags,
        "sources": sorted({card["outlet"] for card in news}),
        "source": "AI industry press",
        "news": news,
    }


def collect(days: int, end: date) -> list[dict[str, Any]]:
    tz = local_now().tzinfo
    cutoff = datetime.combine(end - timedelta(days=days - 1), datetime.min.time(), tz)
    horizon = datetime.combine(end, datetime.max.time(), tz)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_feed, FEEDS))

    collected: list[dict[str, Any]] = []
    for feed, items, error in results:
        if error:
            print(f"  {feed['name']:16} failed: {error}", file=sys.stderr)
            continue
        kept: list[dict[str, Any]] = []
        for item in items:
            when = item["when"]
            if when is None or not (cutoff <= when <= horizon):
                continue
            if not feed["ai_only"] and not RELEVANCE_RE.search(f"{item['title']} {item['summary']}"):
                continue
            item["source"] = feed["name"]
            kept.append(item)
            if len(kept) >= MAX_PER_SOURCE:
                break
        print(f"  {feed['name']:16} {len(kept):>3} of {len(items):>3}")
        collected.extend(kept)
    return collected


def main() -> int:
    global MAX_PER_SOURCE  # noqa: PLW0603 - one knob, set once from argv

    parser = argparse.ArgumentParser(description="Fetch AI industry news into news.json")
    parser.add_argument(
        "--date",
        help="Last day of the window (YYYY-MM-DD). Defaults to today in the local timezone.",
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"Window size (default {DEFAULT_DAYS}).")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path.")
    parser.add_argument("--limit", type=int, default=MAX_PER_SOURCE, help="Max items kept per source.")
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days must be at least 1")

    if args.date:
        try:
            end = date.fromisoformat(args.date)
        except ValueError:
            parser.error(f"could not parse --date {args.date!r}; expected YYYY-MM-DD")
    else:
        end = local_today()

    MAX_PER_SOURCE = args.limit

    print(f"Fetching {len(FEEDS)} feeds for the {args.days} day(s) ending {end}:")
    items = collect(args.days, end)
    if not items:
        print("No news found. Leaving any existing news.json untouched.", file=sys.stderr)
        return 1

    payload = build_payload(items, end - timedelta(days=args.days - 1), end)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    multi = sum(1 for n in payload["news"] if n["outlet_count"] > 1)
    print(f"\nWrote {payload['count']} stories from {len(items)} items "
          f"({len(items) - payload['count']} merged as duplicates, {multi} covered by 2+ outlets) to {out_path}")
    print("Topics: " + ", ".join(f"{t['name']} ({t['count']})" for t in payload["tags"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
