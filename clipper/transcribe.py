"""Speech to text with word timestamps.

Order in "auto" mode: a sidecar file next to the source (tests, or a transcript the buyer sent),
Groq Whisper, then local faster-whisper.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import requests

from . import media
from .config import Settings
from .models import Word

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
CHUNK_SECONDS = 600.0


class TranscriptionError(RuntimeError):
    pass


def transcribe(src: Path, workdir: Path, settings: Settings, duration: float,
               silences: list[tuple[float, float]] | None = None) -> tuple[list[Word], str]:
    """Returns (words, engine name)."""
    mode = settings.transcriber
    sidecar = sidecar_path(src)
    if mode in ("auto", "sidecar") and sidecar.exists():
        return load_sidecar(sidecar), "sidecar"
    if mode == "sidecar":
        raise TranscriptionError(f"no sidecar transcript at {sidecar}")
    errors = []
    if mode in ("auto", "groq") and settings.groq_api_key:
        try:
            return _groq(src, workdir, settings, duration, silences or []), f"groq:{settings.groq_whisper_model}"
        except Exception as e:  # fall through to local
            log.warning("groq transcription failed: %s", e)
            errors.append(f"groq: {e}")
    if mode in ("auto", "local"):
        try:
            return _local(src, settings), f"faster-whisper:{settings.local_whisper_model}"
        except ImportError:
            errors.append("local: faster-whisper not installed (pip install '.[local-whisper]')")
        except Exception as e:
            errors.append(f"local: {e}")
    if not settings.groq_api_key and mode in ("auto", "groq"):
        errors.insert(0, "GROQ_API_KEY is not set")
    raise TranscriptionError("; ".join(errors) or "no transcriber available")


def sidecar_path(src: Path) -> Path:
    return src.with_suffix(".words.json")


def load_sidecar(path: Path) -> list[Word]:
    data = json.loads(path.read_text())
    return [Word(w["text"], float(w["start"]), float(w["end"])) for w in data]


def _chunk_points(duration: float, silences: list[tuple[float, float]]) -> list[float]:
    """Chunk boundaries near every CHUNK_SECONDS, moved into a silence so no word is split."""
    points = [0.0]
    t = CHUNK_SECONDS
    while t < duration - 30:
        best = t
        near = [(s + e) / 2 for s, e in silences if abs((s + e) / 2 - t) < 45]
        if near:
            best = min(near, key=lambda m: abs(m - t))
        points.append(best)
        t = best + CHUNK_SECONDS
    points.append(duration)
    return points


def _groq(src: Path, workdir: Path, settings: Settings, duration: float,
          silences: list[tuple[float, float]]) -> list[Word]:
    points = _chunk_points(duration, silences)
    words: list[Word] = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        chunk = media.extract_audio(src, workdir / f"audio_{i:03d}.mp3", start=a, duration=b - a)
        data = _groq_request(chunk, settings)
        words.extend(_words_from_verbose(data, offset=a))
    return words


def _groq_request(path: Path, settings: Settings) -> dict:
    for attempt in range(5):
        with open(path, "rb") as f:
            r = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                files={"file": (path.name, f, "audio/mpeg")},
                data=[("model", settings.groq_whisper_model), ("response_format", "verbose_json"),
                      ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment"),
                      ("temperature", "0")],
                timeout=300,
            )
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("retry-after") or 2 ** (attempt + 2))
            log.info("groq busy (%s), retrying in %.0fs", r.status_code, wait)
            time.sleep(min(wait, 90))
            continue
        if not r.ok:
            raise TranscriptionError(f"groq answered {r.status_code}: {r.text[:300]}")
        return r.json()
    raise TranscriptionError("groq rate limit did not clear")


def _norm(s: str) -> str:
    return re.sub(r"[^\w']", "", s.lower())


def _words_from_verbose(data: dict, offset: float = 0.0) -> list[Word]:
    """Whisper word lists have no punctuation; borrow it from the segment text so sentences can be found."""
    raw = [Word(w["word"].strip(), float(w["start"]) + offset, float(w["end"]) + offset)
           for w in data.get("words") or [] if w.get("word", "").strip()]
    tokens = [t for seg in data.get("segments") or [] for t in seg.get("text", "").split()]
    j = 0
    for w in raw:
        target = _norm(w.text)
        for k in range(j, min(j + 4, len(tokens))):
            if _norm(tokens[k]) == target and target:
                w.text = tokens[k]
                j = k + 1
                break
    return raw


def _local(src: Path, settings: Settings) -> list[Word]:
    from faster_whisper import WhisperModel  # optional dependency

    model = WhisperModel(settings.local_whisper_model, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(src), word_timestamps=True, vad_filter=True)
    out: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            if w.word.strip():
                out.append(Word(w.word.strip(), float(w.start), float(w.end)))
    return out
