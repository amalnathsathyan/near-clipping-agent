"""The clipping pipeline: source in, clips + report out. Also handles per-clip revisions.

Everything a revision needs (transcript, heatmap, plans, renders) is saved in the job folder, so a
change request days later only re-renders the clips that were named.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import media, report, revision, selection, transcribe
from .boundaries import build_sentences, clip_words, sentence_range_for, snap, words_cut
from .brief import describe
from .config import Settings
from .models import Brief, ClipPlan, HeatPoint, RenderResult, Sentence, Word
from .reframe import FaceTrack, track_faces
from .render import render_clip

log = logging.getLogger(__name__)
Progress = Callable[[str], None]


class JobError(RuntimeError):
    """A failure we can explain to the buyer in plain words."""


@dataclass
class JobState:
    brief: Brief
    title: str = ""
    source_url: str | None = None
    source_path: str = ""
    duration: float = 0.0
    has_heatmap: bool = False
    transcriber: str = ""
    selector: str = ""
    clips: list[ClipPlan] = field(default_factory=list)
    renders: list[RenderResult] = field(default_factory=list)
    version: int = 0
    history: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def save(self, job_dir: Path) -> None:
        (job_dir / "state.json").write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, job_dir: Path) -> "JobState":
        d = json.loads((job_dir / "state.json").read_text())
        st = cls(brief=Brief(**d.pop("brief")))
        st.clips = [ClipPlan(**c) for c in d.pop("clips")]
        st.renders = [RenderResult(**r) for r in d.pop("renders")]
        for k, v in d.items():
            setattr(st, k, v)
        return st


# ---------------------------------------------------------------- analysis (cached per job)

def _energy_per_second(src: Path) -> list[float]:
    """Loudness per second as a z-score; used only as a tie-breaker."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "8000",
                           "-f", "s16le", "-"], capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return []
    a = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    n = len(a) // 8000
    if n < 2:
        return []
    rms = np.sqrt((a[: n * 8000].reshape(n, 8000) ** 2).mean(axis=1) + 1e-9)
    db = 20 * np.log10(rms + 1e-9)
    z = (db - db.mean()) / (db.std() + 1e-6)
    return [float(x) for x in z]


@dataclass
class Analysis:
    probe: media.Probe
    words: list[Word]
    sentences: list[Sentence]
    silences: list[tuple[float, float]]
    energy: list[float]
    heat: list[HeatPoint]


def analyze(job_dir: Path, st: JobState, settings: Settings, progress: Progress) -> Analysis:
    src = Path(st.source_path)
    info = media.probe(src)
    cache = job_dir / "analysis.json"
    if cache.exists():
        d = json.loads(cache.read_text())
        words = [Word(**w) for w in d["words"]]
        silences = [tuple(s) for s in d["silences"]]
        energy = d["energy"]
        heat = [HeatPoint(**h) for h in d["heat"]]
    else:
        progress("Analysing audio")
        silences = media.detect_silences(src)
        energy = _energy_per_second(src)
        heat = [HeatPoint(**h) for h in json.loads((job_dir / "heatmap.json").read_text())] \
            if (job_dir / "heatmap.json").exists() else []
        progress("Transcribing")
        t0 = time.time()
        words, engine = transcribe.transcribe(src, job_dir, settings, info.duration, silences)
        st.transcriber = engine
        st.timings["transcribe_s"] = round(time.time() - t0, 1)
        cache.write_text(json.dumps({"words": [asdict(w) for w in words], "silences": silences,
                                     "energy": energy, "heat": [asdict(h) for h in heat]}))
    if not words:
        raise JobError("I couldn't find any speech in this video, so there's nothing to caption or "
                       "choose moments from. This agent works on talking videos (podcasts, interviews, talks).")
    return Analysis(info, words, build_sentences(words), silences, energy, heat)


# ---------------------------------------------------------------- full job

def run_job(brief: Brief, job_dir: Path, settings: Settings, progress: Progress = lambda s: None,
            source_headers: dict | None = None) -> JobState:
    job_dir.mkdir(parents=True, exist_ok=True)
    st = JobState(brief=brief)
    t_start = time.time()
    if not brief.source:
        raise JobError("No video was found in the request. Please attach the file or paste a link.")

    progress("Fetching the video")
    try:
        fetched = media.fetch_source(brief.source, job_dir / "source", cookies=settings.ytdlp_cookies,
                                     max_minutes=settings.max_source_minutes, headers=source_headers)
    except media.MediaError as e:
        raise JobError(str(e)) from e
    except Exception as e:
        msg = str(e)
        if "Sign in to confirm" in msg or "bot" in msg.lower():
            raise JobError("YouTube refused the download from this server (bot check). Please upload "
                           "the video file directly and I'll process it right away.") from e
        raise JobError(f"I couldn't download that link: {msg[:300]}") from e
    st.title, st.source_url, st.source_path = fetched.title, fetched.url, str(fetched.path)
    st.has_heatmap = bool(fetched.heatmap)
    (job_dir / "heatmap.json").write_text(json.dumps([asdict(h) for h in fetched.heatmap]))
    st.timings["fetch_s"] = round(time.time() - t_start, 1)

    info = media.probe(fetched.path)
    st.duration = info.duration
    if info.duration > settings.max_source_minutes * 60:
        raise JobError(f"The video is {info.duration / 60:.0f} minutes; this agent takes up to "
                       f"{settings.max_source_minutes} minutes.")
    if not info.has_audio:
        raise JobError("The video has no audio track, so there's no speech to caption or choose from.")

    an = analyze(job_dir, st, settings, progress)
    if st.duration < brief.min_len:
        st.notes.append(f"The video is only {st.duration:.0f} s long, shorter than the requested clip length, "
                        f"so the clip covers the whole video.")
    progress("Choosing moments")
    t0 = time.time()
    plans, st.selector = selection.pick_clips(an.words, an.sentences, brief, an.heat, an.energy, st.title,
                                              settings, an.silences, an.probe.duration)
    st.timings["select_s"] = round(time.time() - t0, 1)
    if len(plans) < brief.clips:
        st.notes.append(f"You asked for {brief.clips} clips; this video only has {len(plans)} non-overlapping "
                        f"moments that fit {brief.min_len:.0f}-{brief.max_len:.0f} s, so I delivered "
                        f"{len(plans)} rather than pad with weak ones.")
    st.clips = plans

    st.version = 1
    progress(f"Rendering {len(plans)} clips x {len(brief.formats)} formats")
    t0 = time.time()
    st.renders = _render_all(an, st, plans, job_dir, settings)
    st.timings["render_s"] = round(time.time() - t0, 1)
    st.checks = verify(st, an)
    st.timings["total_s"] = round(time.time() - t_start, 1)
    st.history.append({"version": 1, "at": time.time(), "summary": "first delivery"})
    st.save(job_dir)
    return st


def _render_all(an: Analysis, st: JobState, plans: list[ClipPlan], job_dir: Path, settings: Settings) -> list[RenderResult]:
    out_dir = job_dir / "out" / f"v{st.version}"
    results = []
    src = Path(st.source_path)
    for p in plans:
        track = track_faces(src, p.start, p.end) if an.probe.has_video else FaceTrack()
        for fmt in st.brief.formats:
            results.append(render_clip(src, an.probe, p, fmt, an.words, track, out_dir, settings, title=p.title))
    for f in st.brief.unsupported_formats:
        for p in plans:
            results.append(RenderResult(p.id, f, None, False,
                                        f"{f} isn't a format this agent renders (supported: 9:16, 1:1, 4:5, 16:9)"))
    return results


# ---------------------------------------------------------------- checks

def verify(st: JobState, an: Analysis) -> list[dict]:
    checks = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    wanted = len(st.clips) * len(st.brief.formats)
    made = [r for r in st.renders if r.ok]
    add("file count", len(made) == wanted, f"{len(made)} of {wanted} files rendered")
    for c in st.clips:
        cut_in = words_cut(an.words, c.start) + words_cut(an.words, c.end)
        add(f"{c.id} clean cuts", not cut_in,
            "no word is cut" if not cut_in else "cuts through: " + ", ".join(w.text for w in cut_in))
        sents_start = any(abs(s.start - c.start) < 0.6 and s.start >= c.start for s in an.sentences)
        sents_end = any(abs(s.end - c.end) < 0.6 and s.end <= c.end for s in an.sentences)
        add(f"{c.id} sentence boundaries", sents_start and sents_end,
            "starts and ends on a sentence" if sents_start and sents_end else "boundary not on a sentence edge")
        in_range = st.brief.min_len * 0.75 <= c.duration <= st.brief.max_len + 2 or c.duration >= st.duration - 1
        add(f"{c.id} length", in_range, f"{c.duration:.1f} s")
    for r in made:
        try:
            p = media.probe(r.path)
            exp = next(c for c in st.clips if c.id == r.clip_id).duration
            add(f"{r.clip_id} {r.fmt} file", p.has_video and p.has_audio and abs(p.duration - exp) < 1.0,
                f"{p.width}x{p.height}, {p.duration:.1f} s")
        except Exception as e:
            add(f"{r.clip_id} {r.fmt} file", False, str(e)[:200])
    return checks


# ---------------------------------------------------------------- revisions

def revise_job(job_dir: Path, request_text: str, settings: Settings,
               progress: Progress = lambda s: None) -> tuple[JobState, list[str], revision.ChangeRequest]:
    st = JobState.load(job_dir)
    an = analyze(job_dir, st, settings, progress)
    req = revision.parse(request_text, [asdict(c) for c in st.clips], settings)
    if not req.edits:
        return st, [], req

    by_id = {c.id: c for c in st.clips}
    changed: dict[str, list[str]] = {}
    replace_ids = []
    for e in req.edits:
        c = by_id[e.clip]
        what = changed.setdefault(c.id, [])
        if e.action == "replace":
            replace_ids.append(c.id)
            continue
        if e.action in ("bigger_captions", "smaller_captions"):
            c.caption_scale = round(c.caption_scale * (1.25 if e.action == "bigger_captions" else 0.8), 2)
            what.append(f"captions {'bigger' if e.action == 'bigger_captions' else 'smaller'}")
            continue
        if e.action in ("no_captions", "add_captions"):
            c.captions = e.action == "add_captions"
            what.append("captions on" if c.captions else "captions removed")
            continue
        s_from, s_to = sentence_range_for(an.sentences, c.start, c.end)
        secs = e.seconds or 0
        if e.action == "set_range":
            a = e.start if e.start is not None else c.start
            b = e.end if e.end is not None else c.end
            s_from, s_to = sentence_range_for(an.sentences, a, b)
        elif e.action == "extend_start":
            s_from = _move(an.sentences, s_from, -1, secs, edge="start")
        elif e.action == "trim_start":
            s_from = min(s_to, _move(an.sentences, s_from, +1, secs, edge="start"))
        elif e.action == "extend_end":
            s_to = _move(an.sentences, s_to, +1, secs, edge="end")
        elif e.action == "trim_end":
            s_to = max(s_from, _move(an.sentences, s_to, -1, secs, edge="end"))
        else:
            continue
        old = (c.start, c.end)
        c.start, c.end = snap(an.words, an.sentences, s_from, s_to, an.silences, an.probe.duration)
        _refresh_reason(c, an, st)
        what.append(f"{_fmt(old[0])}-{_fmt(old[1])} -> {_fmt(c.start)}-{_fmt(c.end)}")

    if replace_ids:
        keep = [(c.start, c.end) for c in st.clips if c.id not in replace_ids]
        # Also avoid the moments being replaced: the buyer didn't want them.
        avoid = keep + [(by_id[i].start, by_id[i].end) for i in replace_ids]
        new, _ = selection.pick_clips(an.words, an.sentences, st.brief, an.heat, an.energy, st.title, settings,
                                      an.silences, an.probe.duration, exclude=avoid, n=len(replace_ids))
        for cid, fresh in zip(replace_ids, new):
            old = by_id[cid]
            fresh.id = cid
            fresh.caption_scale, fresh.captions = old.caption_scale, old.captions
            idx = st.clips.index(old)
            st.clips[idx] = fresh
            by_id[cid] = fresh
            changed.setdefault(cid, []).append(f"replaced with a new moment at {_fmt(fresh.start)}-{_fmt(fresh.end)}")
        for cid in replace_ids[len(new):]:
            changed.setdefault(cid, []).append("no other suitable moment left; kept as it was")

    ids = [cid for cid, w in changed.items() if w]
    progress(f"Re-rendering {', '.join(ids)}")
    st.version += 1
    out_dir = job_dir / "out" / f"v{st.version}"
    src = Path(st.source_path)
    new_renders = []
    for r in st.renders:
        if r.clip_id in ids:
            continue
        new_renders.append(r)  # unchanged clips keep their files (and uploaded URLs)
    for cid in ids:
        c = by_id[cid]
        track = track_faces(src, c.start, c.end) if an.probe.has_video else FaceTrack()
        for fmt in st.brief.formats:
            new_renders.append(render_clip(src, an.probe, c, fmt, an.words, track, out_dir, settings, title=c.title))
    order = {c.id: i for i, c in enumerate(st.clips)}
    st.renders = sorted(new_renders, key=lambda r: (order.get(r.clip_id, 99), r.fmt))
    st.checks = verify(st, an)
    summary = [f"{cid}: {'; '.join(changed[cid])}" for cid in ids]
    st.history.append({"version": st.version, "at": time.time(), "summary": summary, "request": request_text[:2000]})
    st.save(job_dir)
    return st, summary, req


def _move(sents: list[Sentence], idx: int, step: int, secs: float, edge: str) -> int:
    """Move a sentence index by at least one sentence, or far enough to cover `secs` seconds."""
    t0 = sents[idx].start if edge == "start" else sents[idx].end
    j = idx
    while 0 <= j + step < len(sents):
        j += step
        t = sents[j].start if edge == "start" else sents[j].end
        if not secs or abs(t - t0) >= secs * 0.8:
            break
    return j


def _refresh_reason(c: ClipPlan, an: Analysis, st: JobState) -> None:
    text = " ".join(w.text for w in clip_words(an.words, c.start, c.end))
    if selection._norm(c.quote) not in selection._norm(text):
        s_from, s_to = sentence_range_for(an.sentences, c.start, c.end)
        sents = an.sentences[s_from:s_to + 1]
        c.quote = selection._best_line(sents)
        c.reason = selection._heuristic_reason(sents, c.quote)


def _fmt(t: float) -> str:
    from .boundaries import fmt_ts
    return fmt_ts(t)


def cleanup_sources(job_dir: Path) -> None:
    shutil.rmtree(job_dir / "source", ignore_errors=True)


__all__ = ["run_job", "revise_job", "JobState", "JobError", "describe", "report"]
