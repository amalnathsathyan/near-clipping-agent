"""Turn a buyer's free-text request into a Brief (clip count, formats, captions, length, source)."""

from __future__ import annotations

import re

from .models import FORMATS, Brief

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a few": 3}
MAX_CLIPS = 20

FORMAT_WORDS = [
    (r"\b9\s*[:x/]\s*16\b|\bvertical\b|\bshorts?\b|\btik\s?toks?\b|\breels?\b|\bstories\b", "9:16"),
    (r"\b1\s*[:x/]\s*1\b|\bsquare\b", "1:1"),
    (r"\b4\s*[:x/]\s*5\b|\bfeed post\b", "4:5"),
    (r"\b16\s*[:x/]\s*9\b|\bhorizontal\b|\blandscape\b|\bwidescreen\b", "16:9"),
]
URL_RE = re.compile(r"https?://[^\s<>()\"'\]]+", re.I)


def parse_brief(text: str) -> Brief:
    b = Brief(raw=text)
    low = text.lower()

    # Source link: prefer a video platform, skip marketplace links.
    urls = [u.rstrip(".,;)") for u in URL_RE.findall(text)]
    urls = [u for u in urls if "market.near.ai" not in u]
    vids = [u for u in urls if re.search(r"youtube\.com|youtu\.be|vimeo\.com|loom\.com|twitch\.tv|x\.com/.+/video|"
                                         r"\.(mp4|mov|mkv|webm|m4v|mp3|m4a|wav)(\?|$)", u, re.I)]
    b.source = (vids or urls or [None])[0]

    # Clip count
    num = r"(\d{1,2}|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")"
    m = re.search(num + r"\s+(?:[\w-]+\s+){0,3}?(clips?|shorts|highlights?|reels|moments|cuts)\b", low)
    if m:
        v = m.group(1)
        b.clips = max(1, min(MAX_CLIPS, int(v) if v.isdigit() else NUM_WORDS[v]))
    else:
        b.defaults_used.append("5 clips")

    # Formats
    fmts = []
    for pat, f in FORMAT_WORDS:
        if re.search(pat, low) and f not in fmts:
            fmts.append(f)
    if "all formats" in low or "every format" in low or "all platforms" in low:
        fmts = list(FORMATS)
    for m2 in re.finditer(r"(?:format|aspect|ratio|size)[^.\n]{0,25}?\b(\d{1,2})\s*:\s*(\d{1,2})\b|"
                          r"\b(\d{1,2})\s*:\s*(\d{1,2})\b[^.\n]{0,15}?(?:format|aspect|ratio)", low):
        a, c = (m2.group(1), m2.group(2)) if m2.group(1) else (m2.group(3), m2.group(4))
        f = f"{int(a)}:{int(c)}"
        if f not in FORMATS and f not in b.unsupported_formats:
            b.unsupported_formats.append(f)
    if fmts:
        b.formats = [f for f in ["9:16", "1:1", "4:5", "16:9"] if f in fmts]
    else:
        b.defaults_used.append("vertical 9:16")

    # Captions
    if re.search(r"\b(no|without|skip|don'?t (?:want|need|add))\s+(captions?|subtitles?|subs|text)\b", low):
        b.captions = False
    elif not re.search(r"captions?|subtitles?", low):
        b.defaults_used.append("burned-in captions")

    # Length
    m = re.search(r"(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})\s*(?:s\b|sec|secs|seconds)", low)
    if m:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        b.min_len, b.max_len = float(max(5, lo)), float(max(lo + 5, hi))
    else:
        m = re.search(r"(?:under|max(?:imum)?|up to|no longer than|less than|<)\s*(\d{1,3})\s*(s\b|sec|secs|seconds|min|minutes?)", low)
        m1 = re.search(r"(\d{1,3})\s*[- ]?(s\b|sec|secs|second|seconds|min|minute)\b", low)
        if m:
            v = int(m.group(1)) * (60 if m.group(2).startswith("min") else 1)
            b.max_len = float(max(10, min(180, v)))
            b.min_len = float(min(b.min_len, max(8, b.max_len * 0.5)))
        else:
            v = 0
            if m1 and re.search(r"(clips?|long|each|length)", low[m1.end():m1.end() + 25] + low[max(0, m1.start() - 25):m1.start()]):
                v = int(m1.group(1)) * (60 if m1.group(2).startswith("min") else 1)
            if 5 <= v <= 180:
                b.min_len, b.max_len = float(max(5, round(v * 0.7))), float(round(v * 1.15))
            else:
                b.defaults_used.append("20-60 s per clip")

    if re.search(r"\b(i|we) (own|have (the )?rights|have permission)|\bmy (own )?(video|channel|podcast)\b|"
                 r"\brights (are )?confirmed\b", low):
        b.rights_confirmed = True

    b.hints = text.strip()[:1500]
    return b


def describe(b: Brief) -> str:
    caps = "burned-in captions" if b.captions else "no captions"
    return (f"{b.clips} clip{'s' if b.clips != 1 else ''}, {b.min_len:.0f}-{b.max_len:.0f} s each, "
            f"format{'s' if len(b.formats) > 1 else ''} {', '.join(b.formats)}, {caps}")
