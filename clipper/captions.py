"""Burned-in captions as ASS subtitles, timed from Whisper word timestamps.

Phone-first: big bold type with a heavy outline, 2-4 words per line, the spoken word highlighted,
and kept above the bottom area that TikTok / Reels / Shorts cover with their own buttons.
"""

from __future__ import annotations

import re

from .models import Word

HIGHLIGHT = "&H0000E5FF&"  # ASS is BGR: warm yellow
MAX_WORDS = 4
MAX_CHARS = 18
PAUSE_BREAK = 0.35


def chunk_words(words: list[Word]) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        chars = sum(len(x.text) + 1 for x in cur)
        brk = (
            nxt is None
            or len(cur) >= MAX_WORDS
            or chars + len(nxt.text) > MAX_CHARS and len(cur) >= 2
            or w.text.endswith((".", "?", "!", ",", ";", ":"))
            or nxt.start - w.end > PAUSE_BREAK
        )
        if brk:
            chunks.append(cur)
            cur = []
    return chunks


def font_size(width: int, height: int, scale: float = 1.0) -> int:
    return int(round(min(0.075 * height, 0.085 * width) * scale))


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(s: str) -> str:
    return re.sub(r"[{}\\]", "", s)


def build_ass(words: list[Word], clip_start: float, clip_end: float, width: int, height: int,
              font: str, scale: float = 1.0, layout: str = "crop", title: str | None = None) -> str:
    size = font_size(width, height, scale)
    outline = max(3, int(size * 0.09))
    vertical = height > width
    if vertical:
        margin_v = int(height * (0.24 if layout == "crop" else 0.20))
    else:
        margin_v = int(height * 0.08)
    margin_h = int(width * 0.06)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},2,{margin_h},{margin_h},{margin_v},1
Style: Title,{font},{int(size * 0.8)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},0,8,{margin_h},{margin_h},{int(height * 0.10)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    dur = clip_end - clip_start
    if title:
        events.append(f"Dialogue: 0,{_ts(0)},{_ts(dur)},Title,,0,0,0,,{_clean(title)}")
    rel = [Word(w.text, w.start - clip_start, w.end - clip_start) for w in words]
    chunks = chunk_words(rel)
    for ci, chunk in enumerate(chunks):
        nxt_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else dur
        chunk_end = min(chunk[-1].end + 0.35, nxt_start, dur)
        for wi, w in enumerate(chunk):
            a = w.start if wi else chunk[0].start
            b = chunk[wi + 1].start if wi + 1 < len(chunk) else chunk_end
            if b - a < 0.01:
                continue
            parts = []
            for k, x in enumerate(chunk):
                t = _clean(x.text)
                parts.append(f"{{\\c{HIGHLIGHT}}}{t}{{\\c&H00FFFFFF&}}" if k == wi else t)
            events.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(events) + "\n"
