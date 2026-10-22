"""Settings read from the environment (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional at runtime
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


@dataclass
class Settings:
    # Marketplace
    market_base_url: str = field(default_factory=lambda: _env("MARKET_BASE_URL", "https://market.near.ai"))
    market_token: str = field(default_factory=lambda: _env("MARKET_AGENT_TOKEN"))
    poll_seconds: int = field(default_factory=lambda: _int("POLL_SECONDS", 45))
    concurrency: int = field(default_factory=lambda: _int("CONCURRENCY", 1))

    # Models
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-flash-latest"))
    groq_llm_model: str = field(default_factory=lambda: _env("GROQ_LLM_MODEL", "openai/gpt-oss-120b"))
    groq_whisper_model: str = field(default_factory=lambda: _env("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"))
    # "auto" tries Gemini, then Groq, then the built-in heuristic. "gemini", "groq" or "heuristic" pins one.
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto"))
    # "auto" tries Groq, then local faster-whisper. "groq", "local" or "sidecar" pins one.
    transcriber: str = field(default_factory=lambda: _env("TRANSCRIBER", "auto"))
    local_whisper_model: str = field(default_factory=lambda: _env("LOCAL_WHISPER_MODEL", "small"))

    # Media
    work_dir: Path = field(default_factory=lambda: Path(_env("WORK_DIR", "./work")).resolve())
    max_source_minutes: int = field(default_factory=lambda: _int("MAX_SOURCE_MINUTES", 180))
    caption_font: str = field(default_factory=lambda: _env("CAPTION_FONT", "DejaVu Sans"))
    video_crf: int = field(default_factory=lambda: _int("VIDEO_CRF", 21))
    x264_preset: str = field(default_factory=lambda: _env("X264_PRESET", "veryfast"))
    ytdlp_cookies: str = field(default_factory=lambda: _env("YTDLP_COOKIES"))
    keep_days: int = field(default_factory=lambda: _int("KEEP_DAYS", 14))
    target_lufs: float = field(default_factory=lambda: _float("TARGET_LUFS", -14.0))

    @property
    def db_path(self) -> Path:
        return self.work_dir / "state.sqlite3"


def load() -> Settings:
    s = Settings()
    s.work_dir.mkdir(parents=True, exist_ok=True)
    return s
