"""Where to point the crop: face detection with OpenCV's bundled Haar cascade (CPU only).

Per clip we sample about one frame a second, find faces, and take the median position of the
largest face. If faces are spread wider than the crop can hold (two people far apart), the render
falls back to the whole frame over a blurred background instead of cutting someone out.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class FaceTrack:
    cx: float = 0.5  # normalized center of interest
    cy: float = 0.4
    spread: float = 0.0  # normalized horizontal spread of faces across samples
    faces_found: int = 0
    samples: int = 0


def track_faces(path: Path, start: float, end: float, max_samples: int = 30) -> FaceTrack:
    try:
        import cv2
    except ImportError:
        return FaceTrack()
    if not hasattr(cv2, "CascadeClassifier"):  # OpenCV 5 moved Haar cascades out of the main package
        log.warning("OpenCV without Haar cascades; using centre crops")
        return FaceTrack()
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return FaceTrack()
    dur = max(0.1, end - start)
    n = max(3, min(max_samples, int(dur)))
    xs, ys, all_x = [], [], []
    samples = 0
    try:
        for k in range(n):
            t = start + dur * (k + 0.5) / n
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            samples += 1
            h, w = frame.shape[:2]
            scale = 480.0 / w if w > 480 else 1.0
            small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6,
                                             minSize=(int(small.shape[1] * 0.06),) * 2)
            if len(faces) == 0:
                continue
            sw, sh = small.shape[1], small.shape[0]
            for (fx, fy, fw, fh) in faces:
                all_x.append((fx + fw / 2) / sw)
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            xs.append((fx + fw / 2) / sw)
            ys.append((fy + fh / 2) / sh)
    finally:
        cap.release()
    if not xs:
        return FaceTrack(samples=samples)
    # Spread uses the 10th-90th percentile of every face seen, so one false positive doesn't count.
    srt = sorted(all_x)
    lo, hi = srt[int(len(srt) * 0.1)], srt[min(len(srt) - 1, int(len(srt) * 0.9))]
    return FaceTrack(cx=statistics.median(xs), cy=statistics.median(ys), spread=hi - lo,
                     faces_found=len(xs), samples=samples)


def crop_rect(src_w: int, src_h: int, out_w: int, out_h: int, track: FaceTrack) -> tuple[int, int, int, int] | None:
    """(w, h, x, y) for a crop of the target aspect, or None when the frame should be fitted instead."""
    target = out_w / out_h
    src = src_w / src_h
    if abs(src - target) < 0.02:
        return (src_w, src_h, 0, 0)
    if src > target:  # trim the sides
        cw = int(round(src_h * target)) // 2 * 2
        if track.faces_found and track.spread > (cw / src_w) * 0.8:
            return None
        x = int(round(track.cx * src_w - cw / 2))
        x = max(0, min(src_w - cw, x))
        return (cw, src_h, x, 0)
    # trim top and bottom; keep the face in the upper part of the frame
    ch = int(round(src_w / target)) // 2 * 2
    if ch < src_h * 0.6:
        return None  # would throw away too much of a vertical source; fit instead
    y = int(round(track.cy * src_h - ch * 0.4))
    y = max(0, min(src_h - ch, y))
    return (src_w, ch, 0, y)
