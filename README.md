# PaperSwipe

A TikTok-style ML paper discovery feed — swipeable cards, one headline, two sentences, tap to go deeper. Rebuilt daily from [HuggingFace Daily Papers](https://huggingface.co/papers). Hosted free on GitHub Pages.

**Desktop:** a filterable grid of frosted-glass cards on a drifting purple/blue bloom.
**Mobile:** one card at a time — swipe left to skip, swipe right to save.

No backend. No database. No API keys. No external AI calls.

---

## How it works

```
GitHub Actions (daily cron — 08:00 UTC)
        ↓
HuggingFace Daily Papers API      huggingface.co/api/daily_papers
(title, summary, upvotes, authors, arxiv id)
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
| `fetch_papers.py` | `--date 2026-08-07` | Fetch one specific day |
| | `--limit 100` | Max papers to request |
| | `--lookback 10` | Days to walk back when a day is empty |
| | `--no-fallback` | Fail instead of walking back |
| `generate_site.py` | `--out public` | Output directory (default `dist/`) |
| | `--serve --port 8080` | Serve the build after generating |

Weekends and holidays are usually empty on HuggingFace, so `fetch_papers.py`
walks backwards day by day until it finds one with papers rather than shipping
an empty feed.

---

## Features

- **Swipeable deck on mobile** — drag to follow your finger with spring-back,
  SAVE/SKIP stamps that fade in with drag distance, buttons and undo.
- **Grid on desktop** — 2–3 columns, hover lift, Siri-style animated ring.
- **Topic filtering** — 16 topics (Interpretability, Alignment & Safety, RL,
  Reasoning, Agents, Efficiency, …) inferred from title/abstract keywords.
- **Full HF summary** — the card shows two sentences; expand for the whole abstract.
- **Saves** — swipe right or hit Save; kept in `localStorage`, viewable under
  the Saved tab, and synced across tabs. Nothing leaves your device.
- **Resume where you left off** — the deck remembers what you've seen for the
  current day only.
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
│   ├── style.css                 # glass, gradients, deck + grid layouts
│   └── app.js                    # swipe, filters, saves (no dependencies)
├── data/papers.json              # regenerated daily
└── dist/                         # build output (gitignored)
```

### `papers.json` shape

```jsonc
{
  "date": "2026-08-07",
  "date_label": "Friday, August 7, 2026",
  "count": 30,
  "tags": [{ "name": "Agents", "count": 9 }],
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
      "published_label": "Aug 2026",
      "arxiv_url": "https://arxiv.org/abs/2608.01492",
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
- The site's only runtime dependency is the browser. `Jinja2` is build-time only;
  `fetch_papers.py` is pure standard library.

## Cost

GitHub Pages (free) + GitHub Actions (~1 min/day of the 2,000 min/month free
tier) + the HuggingFace API (free, no key). **Total: $0.**
