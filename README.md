# paperswipe

**→ [kunalb123.github.io/paperswipe](https://kunalb123.github.io/paperswipe/)**

A TikTok-style ML paper discovery feed — swipeable cards, one headline, two sentences, tap to go deeper. Rebuilt daily from [HuggingFace Daily Papers](https://huggingface.co/papers), covering the last seven days. Hosted free on GitHub Pages.

**Desktop:** a filterable grid of frosted-glass cards, ranked by upvotes across the week.
**Mobile:** one card at a time — swipe left to skip, swipe right to save.

Monochrome graphite: the accent is the far end of the value scale rather than a
hue, so emphasis comes from contrast. A full light palette follows
`prefers-color-scheme`.

No backend. No database. No API keys. No external AI calls.

---

## How it works

```
GitHub Actions (daily cron — 08:00 UTC)
        ↓
HuggingFace Daily Papers API      huggingface.co/api/daily_papers
(one request per day in the 7-day window;
 title, summary, upvotes, authors, arxiv id)
        ↓
scripts/fetch_papers.py           parse + tag + format → data/papers.json
        ↓
scripts/generate_site.py          Jinja2 template → dist/
        ↓
GitHub Pages
```

The whole day's data is embedded in `index.html`, so the page needs exactly one
request to be fully interactive. Cards are server-rendered, so the feed still
works with JavaScript disabled — it just degrades to a plain responsive grid.

---

## Setup

1. Fork this repo.
2. **Settings → Pages → Source: GitHub Actions.**
3. That's it — no secrets. The workflow runs on push and daily at 08:00 UTC.

Your feed is live at `https://<your-username>.github.io/paperswipe`.

To build a specific day by hand: **Actions → Daily build → Run workflow**, and
optionally pass a date.

---

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/fetch_papers.py                 # → data/papers.json
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
| `generate_site.py` | `--out public` | Output directory (default `dist/`) |
| | `--link-target abstract` | Point Read at the arXiv abstract page instead of the PDF |
| | `--serve --port 8080` | Serve the build after generating |

The API is per-day, so the fetcher requests each day in the window and pools the
results, deduped by arXiv id and ranked by upvotes across the whole week. Weekends
and holidays come back empty and simply contribute nothing; only if every day in
the window is empty does it keep walking backwards rather than ship an empty feed.

Roughly 150 papers a week means a ~240 KB gzipped page. Drop `--days` if you want
it leaner.

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
  Reasoning, Agents, Efficiency, …) inferred from title/abstract keywords.
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

---

## Repo structure

```
paperswipe/
├── .github/workflows/daily.yml   # fetch → build → deploy to Pages
├── scripts/
│   ├── fetch_papers.py           # HF API → data/papers.json (stdlib only)
│   └── generate_site.py          # papers.json + template → dist/
├── templates/index.html          # Jinja2 template
├── site/
│   ├── style.css                 # tokens (light + dark), glass, deck + grid layouts
│   └── app.js                    # swipe, filters, saves (no dependencies)
├── data/papers.json              # regenerated daily
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

---

## Notes

- Swipe gestures use native Pointer Events rather than Hammer.js — the card has
  to track your finger 1:1 and rotate as it moves, which needs raw pointer
  deltas anyway, so the dependency bought nothing.
- `.card__body` scrolls, which makes it the element the browser treats as owning
  touch behaviour — so it needs its own `touch-action: pan-y`. Without it, a swipe
  that starts on the abstract pans the page sideways instead of reaching the deck.
- The site's only runtime dependency is the browser. `Jinja2` is build-time only;
  `fetch_papers.py` is pure standard library.

## Cost

GitHub Pages (free) + GitHub Actions (~1 min/day of the 2,000 min/month free
tier) + the HuggingFace API (free, no key). **Total: $0.**
