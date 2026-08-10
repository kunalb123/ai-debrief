# PaperSwipe

A TikTok-style ML paper discovery feed — swipeable cards, one headline, two sentences, tap to go deeper. Rebuilt daily from HuggingFace Daily Papers. Hosted free on GitHub Pages, accessible from any device.

---

## The Problem

HuggingFace Daily Papers truncates summaries and is a list, not a feed. You can't quickly scan and decide what's worth your time. This fixes that.

---

## What It Does

Every day at 8am UTC, a GitHub Actions workflow:
1. Fetches that day's papers from the HuggingFace Daily Papers API
2. Parses the title, summary, upvote count, authors, and ArXiv link
3. Generates a static site and deploys to GitHub Pages

No API keys. No external AI calls. Just the HuggingFace data, formatted for scanning.

---

## UI Design — Apple Intelligence / Siri Aesthetic

### Desktop
Multi-column card grid (2-3 cards visible at once). Each card is a frosted glass panel floating on a dark gradient background. Inspired by the Apple Intelligence UI:

- **Background:** deep dark (`#0a0a0f`) with a subtle radial gradient bloom in purple/blue
- **Cards:** frosted glass — `backdrop-filter: blur(20px)`, semi-transparent dark surface (`rgba(255,255,255,0.05)`), thin luminous border (`rgba(255,255,255,0.1)`)
- **Glow accent:** soft purple/blue gradient glow on card hover, like the Siri activation ring
- **Typography:** SF Pro Display (system font stack), large title, body text in muted white
- **Corners:** large radius (`24px`)
- **Shadows:** deep soft shadow with colored tint matching the glow
- **Animations:** spring physics on hover lift, smooth fade-in on load

### Mobile
One card at a time, full-width, swipeable. Card fills ~85% of screen height. Swipe left to skip, swipe right to save. Same frosted glass aesthetic, action buttons at bottom, title at top.

```
┌─────────────────────────────────────┐  ← frosted glass card
│                                     │
│  🏷 Mechanistic Interpretability    │  ← topic tag pill
│                                     │
│  GPT-2's indirect object            │  ← title (large, bold)
│  identification relies on just      │
│  3 attention heads                  │
│                                     │
│  ─────────────────────────────────  │
│                                     │
│  Researchers used activation        │  ← HF summary, ~4 sentences
│  patching to show that ablating     │
│  these heads kills the behavior     │
│  while leaving others intact.       │
│                                     │
│  ─────────────────────────────────  │
│                                     │
│  ↑ 847  •  3 authors  •  Apr 2026  │  ← metadata row
│                                     │
│  [ Read paper ]  [ Save ]           │  ← action buttons
└─────────────────────────────────────┘

  ← swipe left to skip    swipe right to save →
```

---

## Architecture

```
GitHub Actions (daily cron — 8am UTC)
        ↓
HuggingFace Daily Papers API
(title, summary, upvotes, authors, arxiv link)
        ↓
Python: parse + format card data → papers.json
        ↓
Python: generate static HTML from template
        ↓
GitHub Pages (free hosting, instant global CDN)
```

No backend. No database. No server. No API keys. Static site rebuilds every morning.

---

## Tech Stack

- **Data:** HuggingFace Daily Papers API (`huggingface.co/api/daily_papers`)
- **Site generation:** Python script → static HTML
- **Hosting:** GitHub Pages
- **Automation:** GitHub Actions (daily cron)
- **UI:** Vanilla HTML/CSS/JS — no framework
  - Swipe gestures: Hammer.js
  - Animations: CSS spring transitions + subtle JS
  - Glass effect: `backdrop-filter`, CSS gradients
  - Responsive: CSS Grid for desktop, single column for mobile

---

## Cost

- GitHub Pages: free
- GitHub Actions: free tier (~2,000 min/month, this uses ~1 min/day)
- HuggingFace API: free, no key required

**Total: $0**

---

## Repo Structure

```
paperswipe/
├── README.md
├── .github/
│   └── workflows/
│       └── daily.yml         # Runs daily: fetch → build → deploy
├── scripts/
│   ├── fetch_papers.py       # Hits HF Daily Papers API, saves papers.json
│   └── generate_site.py      # Builds index.html from papers.json + template
├── templates/
│   └── index.html            # Jinja2 template for the site
├── site/
│   ├── style.css             # Apple Intelligence UI — glass, gradients, animations
│   └── app.js                # Swipe gestures, saves, mobile/desktop layout logic
└── data/
    └── papers.json           # Generated daily — current day's card data
```

---

## Setup

1. Fork this repo
2. Enable GitHub Pages (Settings → Pages → Source: GitHub Actions)
3. The daily workflow handles everything else — no secrets needed

Your feed is live at `https://[your-username].github.io/paperswipe`

---

## Features

**Day 1:**
- Swipeable card feed (mobile: one at a time, desktop: grid)
- Full HF summary on tap/expand
- Direct link to ArXiv paper
- Saves swiped-right papers to local storage
- Topic tag filtering (RL, interpretability, efficiency, alignment — inferred from keywords)

**Later:**
- Search saved papers
- Share a card
- Weekly digest view