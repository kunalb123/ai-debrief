#!/usr/bin/env python3
"""Fetch the last week of papers from the HuggingFace Daily Papers API into data/papers.json.

No API key required. The API is per-day, so we request each day in the window and
pool the results, ranked by upvotes across the whole week. Weekends and holidays
come back empty, which is normal — only if the entire window is empty do we keep
walking backwards looking for a day that isn't.

    python scripts/fetch_papers.py                 # the 7 days ending today (UTC)
    python scripts/fetch_papers.py --days 1        # just today
    python scripts/fetch_papers.py --date 2026-08-05 --days 14
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

# Size of the window the feed covers, counting back from the target day.
DEFAULT_DAYS = 7
# Extra days to keep searching if that whole window came back empty.
MAX_LOOKBACK_DAYS = 10
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3

# %-d (no zero padding) is glibc/BSD only; fall back on Windows.
NO_PAD = "%#d" if sys.platform == "win32" else "%-d"


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


# ---------------------------------------------------------------------------
# LaTeX → plain text
# ---------------------------------------------------------------------------
# arXiv abstracts are LaTeX source, so they arrive full of "$R_{15}$" and
# "\textit{...}". Rather than ship a maths renderer to every visitor, we flatten
# it to Unicode once at build time: subscripts and superscripts become real
# characters, known macros become their symbol, and anything unrecognised loses
# its markup instead of leaking backslashes onto the card.
SUBSCRIPTS = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
    "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "=": "₌", "(": "₍",
    ")": "₎", "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ",
    "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}
SUPERSCRIPTS = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
    "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽",
    ")": "⁾", "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ",
    "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ",
    "n": "ⁿ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}

LATEX_SYMBOLS = {
    # Greek
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # Relations and operators
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "sim": "∼", "simeq": "≃", "equiv": "≡", "propto": "∝",
    "ll": "≪", "gg": "≫", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "in": "∈", "notin": "∉", "cup": "∪", "cap": "∩", "emptyset": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨",
    "sum": "∑", "prod": "∏", "int": "∫", "sqrt": "√", "partial": "∂",
    "nabla": "∇", "infty": "∞", "circ": "∘", "star": "⋆", "bullet": "•",
    "oplus": "⊕", "otimes": "⊗", "perp": "⊥", "angle": "∠", "degree": "°",
    # Arrows
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔",
    "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    # Spacing and punctuation
    "dots": "…", "ldots": "…", "cdots": "⋯", "quad": " ", "qquad": " ",
    "textasciitilde": "~", "textbackslash": "\\",
    # Sizing and grouping hints carry no meaning once the maths is flattened.
    "left": "", "right": "", "big": "", "Big": "", "bigg": "", "Bigg": "",
    "displaystyle": "", "textstyle": "", "nonumber": "", "limits": "",
}

# Macros whose only job is styling or accenting — keep the argument, drop the
# wrapper. Accents (\hat, \bar, …) belong here so they never survive as "hatx".
TRANSPARENT_MACROS = (
    "text|textbf|textit|textrm|texttt|textsc|textsl|emph|mathrm|mathbf|mathit"
    "|mathsf|mathtt|mathcal|mathscr|mathfrak|boldsymbol|bm|operatorname|mbox|hbox"
    "|hat|widehat|bar|overline|underline|tilde|widetilde|vec|dot|ddot|check"
    "|breve|acute|grave"
)
# Cross-reference macros carry nothing a reader wants — drop them with their arg.
DROP_MACRO_RE = re.compile(r"\\(?:label|ref|eqref|cite[a-z]*|footnote)\s*\{[^{}]*\}")
BLACKBOARD = {
    "R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "E": "𝔼", "P": "ℙ",
}

MATH_DELIM_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
FRAC_RE = re.compile(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
BLACKBOARD_RE = re.compile(r"\\mathbb\s*\{\s*([A-Z])\s*\}")
TRANSPARENT_RE = re.compile(r"\\(?:" + TRANSPARENT_MACROS + r")\s*\{([^{}]*)\}")
SCRIPT_BRACED_RE = re.compile(r"([_^])\{([^{}]*)\}")
SCRIPT_BARE_RE = re.compile(r"([_^])([A-Za-z0-9])")
# Outside $...$ an underscore is far more often snake_case than a subscript, so
# the prose pass matches the base token too and only converts when the pair
# really looks like maths (see _is_script_base).
PROSE_SCRIPT_RE = re.compile(r"([A-Za-z0-9]+)([_^])(?:\{([^{}]*)\}|([A-Za-z0-9]))")
# Stops before a closing brace or trailing sentence punctuation, so a link at the
# end of an abstract doesn't swallow the delimiter that follows it.
URL_RE = re.compile(r"(?:https?://|www\.)[^\s{}$\\]*[^\s{}$\\.,;:)\]]")
# A brace pair used purely for grouping shouldn't fuse the words either side.
UNBRACE_RE = re.compile(r"([A-Za-z0-9])?\{([^{}]*)\}")
SYMBOL_RE = re.compile(r"\\([A-Za-z]+)")
# Non-alphabetic macros: `\|` is a norm bar, the rest are spacing commands.
NORM_RE = re.compile(r"\\\|")
SPACING_RE = re.compile(r"\\[,;:!>]")
ESCAPED_CHAR_RE = re.compile(r"\\([%&#_${}])")
LEFTOVER_MACRO_ARG_RE = re.compile(r"\\[A-Za-z]+\s*\{([^{}]*)\}")


def _to_script(body: str, table: dict[str, str]) -> str | None:
    """Render `body` in Unicode sub/superscript, or None if any char has no glyph."""
    if not body:
        return None
    out = []
    for char in body:
        glyph = table.get(char)
        if glyph is None:
            return None
        out.append(glyph)
    return "".join(out)


def _is_script_base(base: str, body: str) -> bool:
    """Does `base_body` outside a maths span read as a variable, not an identifier?

    A one-character base is the classic form (`R_1`, `x_i`). A longer base only
    qualifies when it is all letters and the script is all digits (`CO_2`), which
    still rules out `m09c_surgery`, `vjepa2_1` and the rest of snake_case.
    """
    if len(base) == 1:
        return True
    return base.isalpha() and body.isdigit()


def _flatten_math(expr: str, prose: bool = False) -> str:
    """Turn the inside of a maths span into readable plain text.

    With `prose=True` the same treatment is applied to text that was never
    delimited, where the markup has to be recognised far more cautiously.
    """
    expr = BLACKBOARD_RE.sub(lambda m: BLACKBOARD.get(m.group(1), m.group(1)), expr)
    expr = FRAC_RE.sub(r"\1/\2", expr)
    # Before SYMBOL_RE, which only matches alphabetic macro names.
    expr = NORM_RE.sub("‖", expr)
    expr = SPACING_RE.sub(" ", expr)

    # Styling wrappers can nest, so keep unwrapping until nothing changes.
    for _ in range(4):
        unwrapped = TRANSPARENT_RE.sub(r"\1", expr)
        if unwrapped == expr:
            break
        expr = unwrapped

    def braced_script(match: re.Match[str]) -> str:
        kind, body = match.group(1), match.group(2)
        table = SUBSCRIPTS if kind == "_" else SUPERSCRIPTS
        return _to_script(body, table) or f"{kind}{body}"

    def bare_script(match: re.Match[str]) -> str:
        kind, body = match.group(1), match.group(2)
        table = SUBSCRIPTS if kind == "_" else SUPERSCRIPTS
        return _to_script(body, table) or f"{kind}{body}"

    def prose_script(match: re.Match[str]) -> str:
        base, kind = match.group(1), match.group(2)
        body = match.group(3) if match.group(3) is not None else match.group(4)
        if not _is_script_base(base, body):
            return match.group(0)
        table = SUBSCRIPTS if kind == "_" else SUPERSCRIPTS
        script = _to_script(body, table)
        return base + script if script else f"{base}{kind}{body}"

    if prose:
        expr = PROSE_SCRIPT_RE.sub(prose_script, expr)
    else:
        expr = SCRIPT_BRACED_RE.sub(braced_script, expr)
        expr = SCRIPT_BARE_RE.sub(bare_script, expr)
    expr = SYMBOL_RE.sub(lambda m: LATEX_SYMBOLS.get(m.group(1), m.group(1)), expr)
    return expr


def unlatex(text: str) -> str:
    """Flatten LaTeX markup in an abstract or title to plain Unicode text."""
    if not text or not any(ch in text for ch in "\\$_^{}"):
        return text

    # Project links are full of underscores and carets that mean nothing here, and
    # a mangled URL is worse than raw markup — so hold them out of the whole pass.
    urls: list[str] = []

    def stash(match: re.Match[str]) -> str:
        urls.append(match.group(0))
        return f"\x00{len(urls) - 1}\x00"

    text = URL_RE.sub(stash, text)

    text = DROP_MACRO_RE.sub("", text)
    # Maths spans first, so `$R_{15}$` is handled as maths rather than prose.
    text = MATH_DELIM_RE.sub(
        lambda m: _flatten_math(next(g for g in m.groups() if g is not None)), text
    )
    # Then the same treatment for markup outside any $...$, which is common in
    # abstracts written half in prose — but read far more conservatively.
    text = _flatten_math(text, prose=True)

    text = ESCAPED_CHAR_RE.sub(r"\1", text)
    text = LEFTOVER_MACRO_ARG_RE.sub(r"\1", text)
    text = text.replace("\\\\", " ").replace("~", " ")
    # A truncated abstract can leave an unpaired delimiter behind.
    text = text.replace("$", "")
    def unbrace(match: re.Match[str]) -> str:
        before, body = match.group(1) or "", match.group(2)
        joins_words = bool(before) and (body[:1].isalnum() or body[:1] == "\x00")
        return before + (" " if joins_words else "") + body

    text = UNBRACE_RE.sub(unbrace, text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    return re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], text)


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


def fetch_window(
    end: date, days: int, limit: int, lookback: int, allow_latest: bool = True
) -> tuple[list[date], list[tuple[date, dict[str, Any]]]]:
    """Pool every daily list in the `days`-day window ending at `end`.

    Returns the days that actually had papers alongside every entry tagged with
    the day it was featured, newest day first. An empty day inside the window is
    expected (HuggingFace doesn't publish at weekends) and simply contributes
    nothing; only a completely empty window triggers the backwards walk.
    """
    covered: list[date] = []
    collected: list[tuple[date, dict[str, Any]]] = []

    for offset in range(days):
        day = end - timedelta(days=offset)
        print(f"Fetching daily papers for {day.isoformat()} ...")
        items = fetch_day(day, limit)
        if items:
            print(f"  found {len(items)} papers")
            covered.append(day)
            collected.extend((day, item) for item in items)
        else:
            print("  no papers listed for that day")

    if collected:
        return covered, collected

    if lookback:
        print(f"Nothing in the {days}-day window; walking back for a populated day ...")
    for offset in range(days, days + lookback):
        day = end - timedelta(days=offset)
        print(f"Fetching daily papers for {day.isoformat()} ...")
        items = fetch_day(day, limit)
        if items:
            print(f"  found {len(items)} papers")
            return [day], [(day, item) for item in items]
        print("  no papers listed for that day")

    if not allow_latest:
        return [], []

    print(f"No papers in the last {days + lookback} days; falling back to the latest feed.", file=sys.stderr)
    items = api_get({"limit": limit})
    if not items:
        return [], []
    latest = parse_date(items[0].get("publishedAt")) or end
    return [latest], [(latest, item) for item in items]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def normalize(entry: dict[str, Any], rank: int) -> dict[str, Any] | None:
    paper = entry.get("paper") or {}
    paper_id = clean_text(paper.get("id") or entry.get("id"))
    title = unlatex(clean_text(paper.get("title") or entry.get("title")))
    if not paper_id or not title:
        return None

    summary = unlatex(clean_text(paper.get("summary") or entry.get("summary")))
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


def range_labels(start: date, end: date) -> tuple[str, str]:
    """Human labels for the window, in a long and a mobile-width short form."""
    if start == end:
        return end.strftime(f"%A, %B {NO_PAD}, %Y"), end.strftime(f"%a, %b {NO_PAD}")
    if (start.year, start.month) == (end.year, end.month):
        return (
            f"{start.strftime(f'%B {NO_PAD}')}–{end.strftime(NO_PAD)}, {end.year}",
            f"{start.strftime(f'%b {NO_PAD}')}–{end.strftime(NO_PAD)}",
        )
    if start.year == end.year:
        return (
            f"{start.strftime(f'%B {NO_PAD}')} – {end.strftime(f'%B {NO_PAD}')}, {end.year}",
            f"{start.strftime(f'%b {NO_PAD}')} – {end.strftime(f'%b {NO_PAD}')}",
        )
    return (
        f"{start.strftime(f'%B {NO_PAD}, %Y')} – {end.strftime(f'%B {NO_PAD}, %Y')}",
        f"{start.strftime(f'%b {NO_PAD} %y')} – {end.strftime(f'%b {NO_PAD} %y')}",
    )


def build_payload(
    days_covered: list[date], entries: list[tuple[date, dict[str, Any]]]
) -> dict[str, Any]:
    papers: list[dict[str, Any]] = []
    for index, (day, entry) in enumerate(entries):
        paper = normalize(entry, index)
        if paper is None:
            continue
        # Which day HuggingFace featured it — with a week in the feed this is
        # more useful on the card than the arXiv publication month.
        paper["daily_date"] = day.isoformat()
        paper["daily_label"] = day.strftime(f"%b {NO_PAD}")
        papers.append(paper)

    # Entries arrive newest day first, so the survivor of a paper featured on
    # several days is the most recent listing.
    papers = dedupe(papers)
    papers.sort(key=lambda p: (-p["upvotes"], p["daily_date"], p["rank"]))
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

    start, end = min(days_covered), max(days_covered)
    date_label, date_short = range_labels(start, end)

    return {
        "date": end.isoformat(),
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "date_label": date_label,
        "date_short": date_short,
        "days": len(days_covered),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(papers),
        "total_upvotes": sum(p["upvotes"] for p in papers),
        "tags": tags,
        "source": "HuggingFace Daily Papers",
        "source_url": f"https://huggingface.co/papers/date/{end.isoformat()}",
        "papers": papers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch HuggingFace Daily Papers into papers.json")
    parser.add_argument("--date", help="Last day of the window (YYYY-MM-DD). Defaults to today in UTC.")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"How many days the feed covers, counting back from --date (default {DEFAULT_DAYS}).",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path.")
    parser.add_argument("--limit", type=int, default=100, help="Max papers to request per day.")
    parser.add_argument(
        "--lookback",
        type=int,
        default=MAX_LOOKBACK_DAYS,
        help="Extra days to try when the whole window is empty.",
    )
    parser.add_argument("--no-fallback", action="store_true", help="Fail instead of walking back past the window.")
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days must be at least 1")

    if args.date:
        target = parse_date(args.date)
        if target is None:
            parser.error(f"could not parse --date {args.date!r}; expected YYYY-MM-DD")
    else:
        target = datetime.now(timezone.utc).date()

    days_covered, entries = fetch_window(
        target,
        args.days,
        args.limit,
        lookback=0 if args.no_fallback else args.lookback,
        allow_latest=not args.no_fallback,
    )

    if not entries:
        print("No papers found. Leaving any existing papers.json untouched.", file=sys.stderr)
        return 1

    payload = build_payload(days_covered, entries)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {payload['count']} papers across {payload['days']} day(s) — {payload['date_label']} — to {out_path}")
    print("Topics: " + ", ".join(f"{t['name']} ({t['count']})" for t in payload["tags"][:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
