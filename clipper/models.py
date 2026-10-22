"""Plain data types shared across the pipeline. Everything serializes to JSON."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

FORMATS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1920, 1080),
}


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Sentence:
    idx: int
    start: float
    end: float
    text: str
    word_start: int  # index into transcript words
    word_end: int  # inclusive


@dataclass
class HeatPoint:
    start: float
    end: float
    value: float  # 0..1, YouTube "Most replayed"


@dataclass
class Brief:
    source: str | None = None  # URL or local path
    clips: int = 5
    formats: list[str] = field(default_factory=lambda: ["9:16"])
    captions: bool = True
    min_len: float = 20.0
    max_len: float = 60.0
    hints: str = ""
    raw: str = ""
    rights_confirmed: bool | None = None
    defaults_used: list[str] = field(default_factory=list)
    unsupported_formats: list[str] = field(default_factory=list)


@dataclass
class ClipPlan:
    id: str  # stable: C1..Cn
    start: float
    end: float
    title: str
    reason: str
    quote: str
    signal: str  # "most_replayed" or "content"
    signal_detail: str = ""
    score: float = 0.0
    caption_scale: float = 1.0
    captions: bool = True

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class RenderResult:
    clip_id: str
    fmt: str
    path: str | None
    ok: bool
    error: str = ""
    layout: str = ""  # "crop" or "fit"
    url: str | None = None  # set after upload
    file_id: str | None = None


def to_dict(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, list):
        return [to_dict(o) for o in obj]
    return obj
