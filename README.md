# paperswipe

**→ [kunalb123.github.io/paperswipe](https://kunalb123.github.io/paperswipe/)**

A TikTok-style AI discovery feed — swipeable cards, one headline, two sentences, tap to go deeper. Rebuilt daily from [HuggingFace Daily Papers](https://huggingface.co/papers) and fifteen AI news desks, covering the last seven days. Hosted free on GitHub Pages.

**Desktop:** a filterable grid of frosted-glass cards.
**Mobile:** one card at a time — swipe left to skip, swipe right to save.

Two feeds share the deck: **research papers**, ranked by HuggingFace upvotes, and
**industry news**, ranked by how many desks covered the story. Build with
`--content papers` or `--content news` for one or the other.

A flat monochrome ground — true black on dark, off-white on light — where the
accent is the far end of the value scale rather than a hue. Colour is spent on
exactly two things: a per-topic hue on the tags, and save/skip. Both themes
follow `prefers-color-scheme`.

No backend. No database. No API keys. No external AI calls.

---

## How it works

```
GitHub Actions (daily cron — 08:00 UTC)
        ↓
HF Daily Papers API                  15 RSS/Atom feeds
huggingface.co/api/daily_papers      (AI desks + general tech desks)
(one request per day in the window)          ↓
        ↓                            scripts/fetch_news.py
scripts/fetch_papers.py              filter + dedup + tag → data/news.json
parse + tag + format → data/papers.json      ↓
        └──────────────┬──────────────────────┘
                       ↓
        scripts/generate_site.py     interleave → Jinja2 → dist/
                       ↓
                 GitHub Pages
```

The whole window's data is embedded in `index.html`, so the page needs exactly one
request to be fully interactive. Cards are server-rendered, so the feed still
works with JavaScript disabled — it just degrades to a plain responsive grid.

The two feeds are interleaved proportionally rather than merged into one ranking:
a paper's upvote count and a story's outlet count measure different things, and
normalising them into a single score would invent a comparison neither number
supports. Drawing from whichever list is further behind its share of the output
keeps the mix even the whole way down.

---

## Setup

1. Fork this repo.
2. **Settings → Pages → Source: GitHub Actions.**
3. That's it — no secrets. The workflow runs on push and daily at 08:00 UTC.

Your feed is live at `https://<your-username>.github.io/paperswipe`.

To build a specific day by hand: **Actions → Daily build → Run workflow**, and
optionally pass a date and which feeds to publish (`mixed`, `papers` or `news`).

---

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/fetch_papers.py                 # → data/papers.json
python scripts/fetch_news.py                   # → data/news.json
python scripts/generate_site.py --serve        # → dist/, served on :8000
```

Useful flags:

| Command | Flag | Effect |
| --- | --- | --- |
| `fetch_papers.py` | `--days 7` | Size of the window, counting back (default 7) |
| | `--date 2026-08-07` | Last day of the window |
| | `--limit 100` | Max papers to request per day |
| | `--lookback 10` | Extra days to try when the whole window is empty |
| | `--no-fallback` | Fail instead of walking back past the window |
| `fetch_news.py` | `--days 7` | Size of the window, counting back (default 7) |
| | `--date 2026-08-07` | Last day of the window |
| | `--limit 12` | Cap on stories kept from any one desk |
| | `--out data/news.json` | Where to write |
| `generate_site.py` | `--content news` | `mixed` (default), `papers` or `news` |
| | `--news data/news.json` | Path to the news payload |
| | `--out public` | Output directory (default `dist/`) |
| | `--link-target abstract` | Point Read at the arXiv abstract page instead of the PDF |
| | `--serve --port 8080` | Serve the build after generating |

The HF API is per-day, so the fetcher requests each day in the window and pools the
results, deduped by arXiv id and ranked by upvotes across the whole week. Weekends
and holidays come back empty and simply contribute nothing; only if every day in
the window is empty does it keep walking backwards rather than ship an empty feed.

News is optional: a missing or unparseable `data/news.json` degrades the build to
papers only, so a bad morning at someone else's CDN can't block the deploy.

A week is roughly 150 papers and 110 stories — a ~326 KB gzipped page, or ~240 KB
for `--content papers` alone. Drop `--days` if you want it leaner.

---

## Features

- **Swipeable deck on mobile** — drag to follow your finger with spring-back,
  SAVE/SKIP stamps that fade in with drag distance, buttons and undo.
- **Grid on desktop** — 2–3 columns, hover lift, an accent light that travels
  the card border on hover.
- **Light and dark** — one token set per theme in `site/style.css`, switched by
  `prefers-color-scheme`. Nothing outside those two `:root` blocks names a colour.
- **Read straight to the PDF** — the card's primary button opens
  `arxiv.org/pdf/<id>`. Build with `--link-target abstract` for the landing page.
- **Topic filtering** — 16 topics (Interpretability, Alignment & Safety, RL,
  Reasoning, Agents, Efficiency, …) inferred from title/abstract keywords. Each
  carries its own hue, set by a single `--pill-hue` in `site/style.css` — add a
  topic there when you add one to `TOPIC_RULES`.
- **Papers and news in one deck** — news cards carry a solid tag instead of an
  outlined one, and are credited by outlet rather than by authors and upvotes.
  The filter row keeps the two families of topics grouped rather than pooled.
- **Full HF summary** — the card shows two sentences; expand for the whole abstract.
- **Readable maths** — abstracts arrive with raw LaTeX in them, so `unlatex()` in
  the fetcher flattens it to Unicode at build time: `$R_{15}$` → `R₁₅`,
  `\mathbb{R}^n` → `ℝⁿ`, `\alpha` → `α`. No maths renderer ships to the browser.
  URLs and `snake_case` identifiers are held out, so a subscript rule can't eat them.
- **Saves** — swipe right or hit Save; kept in `localStorage`, viewable under
  the Saved tab, and synced across tabs. Nothing leaves your device.
- **Resume where you left off** — the deck remembers what you've seen until the
  window rolls over.
- **Keyboard** — `←` skip, `→` save, `Z` undo, `Enter`/`Space` expand.

---

## Customising topics

Topic tags come from `TOPIC_RULES` in [`scripts/fetch_papers.py`](scripts/fetch_papers.py):
an ordered list of `(topic, [regex, ...])`. A match in the title scores 3, a
match in the abstract scores 1; the top-scoring topic always wins and a second
is added only if it also scores strongly. Add your own topic near the top of the
list to make it take precedence on ties.

News uses the same machinery with its own seven-topic list, `NEWS_RULES` in
[`scripts/fetch_news.py`](scripts/fetch_news.py) — Chips & compute, Open weights,
Model release, Policy, Business, Research results, Product — falling back to
`Industry` for anything that matches nothing. Both lists need a matching
`--pill-hue` in [`site/style.css`](site/style.css) when you add to them.

---

## Where the news comes from

Fifteen editorial desks, listed in `FEEDS` at the top of `fetch_news.py`. All of
them are general AI or tech desks — no company is sourced directly, so no lab
can go missing because it happens not to publish a feed. Anthropic, for one, has
no feed at any path, and still lands in the top five most-covered subjects purely
through other people's reporting.

Nine of the fifteen are AI-only sections and are taken wholesale; the six general
tech desks (Techmeme, Bloomberg, NYT, Tom's Hardware, SiliconANGLE, SemiAnalysis)
are passed through a relevance filter first.

The same story landing at six desks is not noise to be suppressed — it *is* the
ranking signal. News has no upvote count, so how many independent desks picked a
story up stands in for importance. Clustering works on a rare-token signature:
each headline is reduced to the words whose document frequency across the whole
batch is low, and two headlines are the same story when they share at least two
of those and half of the shorter one's signature. Common words ("ai", "model",
"openai") carry no evidence and are dropped before the comparison.

Feeds are parsed with regex rather than `ElementTree` on purpose: one malformed
entity anywhere in a strict XML parse costs the entire source, where here it
costs at most the one item.

---

## Repo structure

```
paperswipe/
├── .github/workflows/daily.yml   # fetch → build → deploy to Pages
├── scripts/
│   ├── fetch_papers.py           # HF API → data/papers.json (stdlib only)
│   ├── fetch_news.py             # 15 RSS feeds → data/news.json (stdlib only)
│   └── generate_site.py          # papers.json + news.json + template → dist/
├── templates/index.html          # Jinja2 template
├── site/
│   ├── style.css                 # tokens (light + dark), glass, deck + grid layouts
│   └── app.js                    # swipe, filters, saves (no dependencies)
├── data/
│   ├── papers.json               # regenerated daily
│   └── news.json                 # regenerated daily
└── dist/                         # build output (gitignored)
```

### `papers.json` shape

```jsonc
{
  "date": "2026-08-10",          // last day in the window
  "date_start": "2026-08-04",
  "date_end": "2026-08-10",
  "date_label": "August 4–10, 2026",
  "days": 5,                     // days in the window that had papers
  "count": 158,
  "tags": [{ "name": "Agents", "count": 45 }],
  "papers": [
    {
      "id": "2608.01492",
      "rank": 1,
      "title": "…",
      "summary": "…",        // full HF abstract
      "teaser": "…",         // first two sentences, shown on the card
      "teaser_rest": "…",    // the remainder, revealed on expand
      "tags": ["Agents"],
      "upvotes": 85,
      "authors": ["…"],
      "author_count": 13,
      "byline": "Wang et al.",
      "daily_date": "2026-08-07",  // the day HF featured it
      "daily_label": "Aug 7",
      "published_label": "Aug 2026",
      "arxiv_url": "https://arxiv.org/abs/2608.01492",
      "pdf_url": "https://arxiv.org/pdf/2608.01492",
      "hf_url": "https://huggingface.co/papers/2608.01492"
    }
  ]
}
```

### `news.json` shape

```jsonc
{
  "date_label": "August 5–11, 2026",   // same window fields as papers.json
  "count": 111,
  "source": "AI industry press",
  "sources": ["Ars Technica", "Bloomberg", "…"],  // desks that returned items
  "tags": [{ "name": "Business", "count": 30 }],
  "news": [
    {
      "id": "n4c8ce4dab58e",     // sha1 of the canonical URL
      "kind": "news",
      "title": "…",
      "summary": "…",            // clipped to ~900 chars on a sentence boundary
      "teaser": "…",             // first two sentences, shown on the card
      "teaser_rest": "…",        // the remainder, revealed on expand
      "tags": ["Research results"],
      "outlet": "Ars Technica",  // the desk whose write-up the card shows
      "outlets": ["Ars Technica", "The Verge"],  // everyone in the cluster
      "outlet_count": 2,         // the ranking signal
      "url": "https://…",        // tracking params stripped
      "published": "2026-08-09",
      "published_label": "Aug 9"
    }
  ]
}
```

---

## Notes

- Swipe gestures use native Pointer Events rather than Hammer.js — the card has
  to track your finger 1:1 and rotate as it moves, which needs raw pointer
  deltas anyway, so the dependency bought nothing.
- `.card__body` scrolls, which makes it the element the browser treats as owning
  touch behaviour — so it needs its own `touch-action: pan-y`. Without it, a swipe
  that starts on the abstract pans the page sideways instead of reaching the deck.
- The saved view only rebuilds its cards when the saved list actually changes.
  `applyMode()` runs on every resize, and expanding a card resizes the page — on
  mobile it collapses the address bar — so an unconditional rebuild would throw
  away the expansion the tap just opened and jump the scroll position.
- Several desks put the entire article in `<content:encoded>`; one podcast
  transcript arrived at 60 KB. Since a card only ever shows a teaser plus an
  expandable remainder, summaries are clipped to a sentence boundary at fetch
  time rather than carried into the page and never read.
- The site's only runtime dependency is the browser. `Jinja2` is build-time only;
  `fetch_papers.py` and `fetch_news.py` are pure standard library.

## Cost

GitHub Pages (free) + GitHub Actions (~1 min/day of the 2,000 min/month free
tier) + the HuggingFace API and fifteen public RSS feeds (free, no keys).
**Total: $0.**
