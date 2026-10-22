"""Render one clip in one format with ffmpeg."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from . import media
from .boundaries import clip_words
from .captions import build_ass
from .config import Settings
from .models import FORMATS, ClipPlan, RenderResult, Word
from .reframe import FaceTrack, crop_rect

log = logging.getLogger(__name__)


def fmt_slug(fmt: str) -> str:
    return fmt.replace(":", "x")


def _escape_filter_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render_clip(src: Path, info: media.Probe, plan: ClipPlan, fmt: str, words: list[Word], track: FaceTrack,
                out_dir: Path, settings: Settings, title: str | None = None) -> RenderResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt not in FORMATS:
        return RenderResult(plan.id, fmt, None, False, f"format {fmt} is not supported")
    if not info.has_audio:
        return RenderResult(plan.id, fmt, None, False, "source has no audio track")
    W, H = FORMATS[fmt]
    out =out_dir / f"{plan.id}_{fmt_slug(fmt)}.mp4"
    ass_path = out_dir / f"{plan.id}_{fmt_slug(fmt)}.ass"
    dur = plan.end - plan.start

    if info.has_video:
        rect = crop_rect(info.width, info.height, W, H, track)
        layout = "crop" if rect else "fit"
    else:
        rect, layout = None, "audio"

    ws = clip_words(words, plan.start, plan.end) if plan.captions else []
    ass = build_ass(ws, plan.start, plan.end, W, H, settings.caption_font, plan.caption_scale, layout,
                    title=title if layout == "audio" else None)
    ass_path.write_text(ass, encoding="utf-8")
    subs = f"ass='{_escape_filter_path(ass_path)}'"

    if layout == "crop":
        cw, ch, x, y = rect
        vf = f"[0:v]crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos,setsar=1,{subs},format=yuv420p[v]"
    elif layout == "fit":
        vf = (f"[0:v]split=2[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
              f"eq=brightness=-0.10[bg];"
              f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,{subs},format=yuv420p[v]")
    else:
        vf = (f"color=c=0x12161c:s={W}x{H}:r=30:d={dur:.3f}[bg];"
              f"[0:a]showwaves=s={W}x{H // 4}:mode=cline:rate=30:colors=0xE5E5E5[w];"
              f"[bg][w]overlay=0:(H-h)/2:shortest=1,setsar=1,{subs},format=yuv420p[v]")
    af = f"[0:a]loudnorm=I={settings.target_lufs}:TP=-1.5:LRA=11,aresample=48000[a]"

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{plan.start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
           "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", settings.x264_preset, "-crf", str(settings.video_crf),
           "-maxrate", "8M", "-bufsize", "16M", "-r", "30",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest"]
    # Encode on local disk, then move into the job folder: faststart rewrites the file, which is slow or
    # unsupported on a mounted storage bucket (Cloud Run).
    with tempfile.TemporaryDirectory(prefix="clip-") as tmp:
        local = Path(tmp) / out.name
        try:
            media.run(cmd + [str(local)], timeout=max(600, dur * 30))
            shutil.move(str(local), out)
        except Exception as e:
            return _failed(plan, fmt, layout, e)
    return RenderResult(plan.id, fmt, str(out), True, "", layout)


def _failed(plan: ClipPlan, fmt: str, layout: str, e: Exception) -> RenderResult:
    """Report the failure instead of dropping the format silently."""
    log.error("render %s %s failed: %s", plan.id, fmt, e)
    return RenderResult(plan.id, fmt, None, False, str(e)[-400:], layout)
