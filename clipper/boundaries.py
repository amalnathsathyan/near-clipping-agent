"""Sentence detection and cut points that never land inside a word or a sentence."""

from __future__ import annotations

from .models import Sentence, Word

END_PUNCT = (".", "?", "!", "…", '."', '?"', '!"')
ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "u.s.", "jr.", "sr."}
LONG_PAUSE = 1.2  # seconds of silence that ends a sentence even without punctuation
MAX_SENTENCE = 25.0  # split run-on sentences (unpunctuated speech) at their biggest pause


def build_sentences(words: list[Word]) -> list[Sentence]:
    groups: list[tuple[int, int]] = []
    start = 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        ends = w.text.endswith(END_PUNCT) and w.text.lower() not in ABBREV
        gap = (words[i + 1].start - w.end) if not last else 0.0
        if last or ends or gap >= LONG_PAUSE:
            groups.append((start, i))
            start = i + 1
    out: list[tuple[int, int]] = []
    for a, b in groups:
        out.extend(_split_long(words, a, b))
    return [
        Sentence(idx=k, start=words[a].start, end=words[b].end,
                 text=" ".join(w.text for w in words[a:b + 1]), word_start=a, word_end=b)
        for k, (a, b) in enumerate(out)
    ]


def _split_long(words: list[Word], a: int, b: int) -> list[tuple[int, int]]:
    if words[b].end - words[a].start <= MAX_SENTENCE or b - a < 4:
        return [(a, b)]
    # Prefer a comma, otherwise the biggest pause, away from the edges.
    best, best_score = None, -1.0
    for i in range(a + 2, b - 1):
        gap = words[i + 1].start - words[i].end
        score = gap + (0.5 if words[i].text.endswith((",", ";", ":")) else 0.0)
        if score > best_score:
            best, best_score = i, score
    if best is None:
        return [(a, b)]
    return _split_long(words, a, best) + _split_long(words, best + 1, b)


def snap(words: list[Word], sentences: list[Sentence], s_from: int, s_to: int,
         silences: list[tuple[float, float]] | None = None, media_end: float | None = None) -> tuple[float, float]:
    """Cut points for sentences s_from..s_to: inside the pause before the first word and after the last."""
    first = sentences[s_from].word_start
    last = sentences[s_to].word_end
    prev_end = words[first - 1].end if first > 0 else 0.0
    next_start = words[last + 1].start if last + 1 < len(words) else (media_end or words[last].end + 1.0)
    w0, w1 = words[first], words[last]

    start = w0.start - min(0.3, max(0.0, (w0.start - prev_end) * 0.6))
    end = w1.end + min(0.45, max(0.0, (next_start - w1.end) * 0.6))
    if silences:
        # Land in a detected silence when one sits in the gap: cleaner audio at the cut.
        for s, e in silences:
            if prev_end - 0.01 <= e <= w0.start + 0.05 and e - s > 0.1:
                start = max(start, min(e - 0.05, w0.start - 0.02))
            if w1.end - 0.05 <= s < next_start and e - s > 0.1:
                cand = max(s + 0.2, w1.end + 0.05)
                if cand < next_start - 0.02:
                    end = min(end, cand)
    start = max(0.0, start)
    if media_end:
        end = min(end, media_end)
    return round(start, 3), round(end, 3)


def words_cut(words: list[Word], t: float) -> list[Word]:
    """Words that a cut at time t would slice through."""
    return [w for w in words if w.start + 0.02 < t < w.end - 0.02]


def clip_words(words: list[Word], start: float, end: float) -> list[Word]:
    return [w for w in words if w.start >= start - 0.01 and w.end <= end + 0.01]


def sentence_range_for(sentences: list[Sentence], start: float, end: float) -> tuple[int, int]:
    """Sentences fully or mostly inside [start, end]."""
    inside = [s.idx for s in sentences if s.start >= start - 0.5 and s.end <= end + 0.5]
    if inside:
        return inside[0], inside[-1]
    mid = (start + end) / 2
    nearest = min(sentences, key=lambda s: abs((s.start + s.end) / 2 - mid))
    return nearest.idx, nearest.idx


def fmt_ts(t: float) -> str:
    t = max(0, int(round(t)))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
