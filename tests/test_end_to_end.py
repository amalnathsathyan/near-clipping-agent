"""Offline end-to-end runs on a synthetic talking video (needs ffmpeg and espeak-ng)."""

import json
import re
import shutil
from pathlib import Path

import pytest

from clipper import media, pipeline, report
from clipper.brief import parse_brief
from clipper.config import Settings
from clipper.models import HeatPoint

from .fixtures import have_tools, make_fixture

pytestmark = pytest.mark.skipif(not have_tools(), reason="needs ffmpeg and espeak-ng")
FIXTURES = Path(__file__).resolve().parent.parent / "work" / "fixtures"


@pytest.fixture(scope="session")
def talk() -> Path:
    return make_fixture(FIXTURES, "talk")


def settings(tmp: Path) -> Settings:
    s = Settings()
    s.work_dir = tmp
    s.transcriber = "sidecar"
    s.llm_provider = "heuristic"
    s.x264_preset = "ultrafast"
    s.market_token = ""
    return s


def test_upload_scenario_vertical_and_square(talk, tmp_path):
    b = parse_brief("3 clips, vertical and square, 15-30 seconds")
    b.source = str(talk)
    st = pipeline.run_job(b, tmp_path / "job", settings(tmp_path))
    assert len(st.clips) == 3
    assert sum(r.ok for r in st.renders) == 6
    assert all(c["ok"] for c in st.checks), [c for c in st.checks if not c["ok"]]
    for r in st.renders:
        p = media.probe(r.path)
        assert (p.width, p.height) in [(1080, 1920), (1080, 1080)]
    md = report.build(st)
    assert "no retention" in md and md.count("**Why this moment:**") == 3


def test_most_replayed_signal_is_used(talk, tmp_path, monkeypatch):
    real = media.fetch_source

    def fake_fetch(source, dest_dir, **kw):
        f = real(source, dest_dir, **kw)
        heat = [HeatPoint(i * 1.2, i * 1.2 + 1.2, 0.15) for i in range(100)]
        for i in range(70, 76):  # replay peak around 84-91 s
            heat[i].value = 0.95
        f.heatmap, f.url, f.title = heat, "https://www.youtube.com/watch?v=test", "Test talk"
        return f

    monkeypatch.setattr(media, "fetch_source", fake_fetch)
    b = parse_brief("2 vertical clips, 15-30 seconds")
    b.source = str(talk)
    st = pipeline.run_job(b, tmp_path / "job", settings(tmp_path))
    assert st.has_heatmap
    hot = [c for c in st.clips if c.signal == "most_replayed"]
    assert hot and hot[0].start <= 88 <= hot[0].end
    assert "Most replayed" in report.build(st)


def test_change_request_rerenders_only_named_clip(talk, tmp_path):
    b = parse_brief("3 vertical clips, 15-30 seconds")
    b.source = str(talk)
    s = settings(tmp_path)
    job = tmp_path / "job"
    st = pipeline.run_job(b, job, s)
    before = {c.id: (c.start, c.end) for c in st.clips}
    st2, summary, _ = pipeline.revise_job(job, "C2: end later please", s)
    assert summary and summary[0].startswith("C2")
    after = {c.id: (c.start, c.end) for c in st2.clips}
    assert after["C1"] == before["C1"] and after["C3"] == before["C3"]
    assert after["C2"][1] > before["C2"][1]
    v2 = {r.path for r in st2.renders if "/v2/" in (r.path or "")}
    assert v2 and all("C2_" in p for p in v2)


def test_audio_only_source(talk, tmp_path):
    audio = tmp_path / "pod.m4a"
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(talk), "-vn", "-c:a", "aac", str(audio)])
    shutil.copy(talk.with_suffix(".words.json"), audio.with_suffix(".words.json"))
    b = parse_brief("1 vertical clip, 15-25 seconds")
    b.source = str(audio)
    st = pipeline.run_job(b, tmp_path / "job", settings(tmp_path))
    assert st.renders[0].ok and st.renders[0].layout == "audio"
    assert media.probe(st.renders[0].path).has_video


def test_llm_selection_and_reasons(talk, tmp_path, monkeypatch):
    from clipper import llm

    calls = {}

    def fake_complete(system, user, s, max_tokens=4096):
        calls["prompt"] = user
        ids = [int(x) for x in re.findall(r"^\[(\d+)\]", user, re.M)][:2]
        return {"clips": [
            {"candidate": ids[0], "trim_start": 0, "trim_end": 0, "title": "Ship before you are ready",
             "reason": "Gives one blunt rule and backs it with a result.", "quote": "a line that is not in the clip"},
            {"candidate": ids[1], "title": "Second", "reason": "Tells a short story with a twist.", "quote": ""},
        ]}, "fake-llm"

    monkeypatch.setattr(llm, "providers", lambda s: ["fake"])
    monkeypatch.setattr(llm, "complete_json", fake_complete)
    b = parse_brief("2 vertical clips, 15-30 seconds, focus on startup lessons")
    b.source = str(talk)
    s = settings(tmp_path)
    s.llm_provider = "auto"
    st = pipeline.run_job(b, tmp_path / "job", s)
    assert st.selector == "fake-llm"
    assert "focus on startup lessons" in calls["prompt"] and "NO retention data" in calls["prompt"]
    assert st.clips[0].title == "Ship before you are ready"
    # A quote the model invented is replaced with a real line from the clip.
    words = json.loads(talk.with_suffix(".words.json").read_text())
    text = " ".join(w["text"] for w in words if w["start"] >= st.clips[0].start - 0.01 and w["end"] <= st.clips[0].end + 0.01)
    assert st.clips[0].quote in text
