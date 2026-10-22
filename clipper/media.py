"""ffmpeg / ffprobe / yt-dlp helpers."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import HeatPoint

log = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"}


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    log.debug("run: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr[-1500:]}")
    return proc


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    has_video: bool
    has_audio: bool
    fps: float


def probe(path: str | Path) -> Probe:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    data = json.loads(out.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 30.0
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 30.0
    return Probe(
        duration=float(data.get("format", {}).get("duration") or 0.0),
        width=int(v["width"]) if v else 0,
        height=int(v["height"]) if v else 0,
        has_video=v is not None,
        has_audio=a is not None,
        fps=fps,
    )


def extract_audio(src: Path, dst: Path, start: float | None = None, duration: float | None = None) -> Path:
    """16 kHz mono, low bitrate: small enough for transcription APIs."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", str(dst)]
    run(cmd)
    return dst


def detect_silences(src: Path, noise_db: int = -32, min_dur: float = 0.25) -> list[tuple[float, float]]:
    """Silent spans in the source audio as (start, end)."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(src), "-vn", "-af",
         f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    spans: list[tuple[float, float]] = []
    cur = None
    for line in proc.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            cur = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            spans.append((cur, float(m.group(1))))
            cur = None
    return spans


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.I))


def is_youtube(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)/", url, re.I))


@dataclass
class Fetched:
    path: Path
    title: str
    heatmap: list[HeatPoint]
    url: str | None
    uploader: str = ""


def fetch_source(source: str, dest_dir: Path, *, cookies: str = "", max_minutes: int = 180,
                 headers: dict | None = None) -> Fetched:
    """Download a URL (YouTube or any yt-dlp site, or a direct file link) or copy a local file."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not is_url(source):
        p = Path(source).expanduser()
        if not p.exists():
            raise MediaError(f"source file not found: {source}")
        return Fetched(path=p, title=p.stem, heatmap=[], url=None)
    if headers or re.search(r"\.(mp4|mov|mkv|webm|m4v|mp3|m4a|wav)(\?|$)", source, re.I):
        return _download_direct(source, dest_dir, headers or {})
    return _download_ytdlp(source, dest_dir, cookies=cookies, max_minutes=max_minutes)


def _download_direct(url: str, dest_dir: Path, headers: dict) -> Fetched:
    import requests

    name = re.sub(r"[^\w.-]", "_", url.split("?")[0].rsplit("/", 1)[-1]) or "source.mp4"
    out = dest_dir / name
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        if m:
            out = dest_dir / re.sub(r"[^\w.-]", "_", m.group(1))
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return Fetched(path=out, title=out.stem, heatmap=[], url=url)


def _download_ytdlp(url: str, dest_dir: Path, *, cookies: str, max_minutes: int) -> Fetched:
    import yt_dlp

    base_opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if cookies and Path(cookies).exists():
        # yt-dlp writes cookies back on exit; use a copy so a read-only secret mount still works.
        local = dest_dir / "cookies.txt"
        shutil.copy(cookies, local)
        base_opts["cookiefile"] = str(local)
    with yt_dlp.YoutubeDL(base_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    duration = info.get("duration") or 0
    if duration and duration > max_minutes * 60:
        raise MediaError(f"video is {duration / 60:.0f} min; this agent accepts up to {max_minutes} min")
    heat = [HeatPoint(float(h["start_time"]), float(h["end_time"]), float(h["value"]))
            for h in (info.get("heatmap") or []) if "start_time" in h]
    opts = dict(base_opts)
    opts.update({
        # Prefer <=1080p mp4/m4a so ffmpeg work stays fast; fall back to best.
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info2 = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info2))
    if not path.exists():
        cands = sorted(dest_dir.glob("source.*"))
        if not cands:
            raise MediaError("download finished but no file was written")
        path = cands[0]
    return Fetched(path=path, title=info.get("title") or "video", heatmap=heat, url=url,
                   uploader=info.get("uploader") or "")


def ensure_tools() -> list[str]:
    return [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
