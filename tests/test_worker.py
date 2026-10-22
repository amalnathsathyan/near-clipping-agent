"""The marketplace worker against an in-process fake of the Agent Market API."""

from pathlib import Path

import pytest

from clipper import transcribe
from clipper.market import MarketClient
from clipper.worker import Worker

from .fake_market import TOKEN, FakeMarket
from .fixtures import have_tools, make_fixture
from .test_end_to_end import FIXTURES, settings

pytestmark = pytest.mark.skipif(not have_tools(), reason="needs ffmpeg and espeak-ng")


@pytest.fixture
def market():
    m = FakeMarket()
    yield m
    m.close()


def test_hire_deliver_and_change_request(market, tmp_path, monkeypatch):
    talk = make_fixture(FIXTURES, "talk")
    # The buyer's upload lands in the job folder; point the offline transcriber at the fixture's timings.
    monkeypatch.setattr(transcribe, "sidecar_path", lambda src: talk.with_suffix(".words.json"))
    s = settings(tmp_path)
    s.market_base_url, s.market_token = market.base, TOKEN
    worker = Worker(s, MarketClient(market.base, TOKEN))

    att = market.add_attachment(talk)
    aid = market.hire("Clip my talk", "Please make 2 clips, vertical and square, 15-30 seconds. I own this video.",
                      first_message="Video attached.", attachments=[att])
    worker.poll_once(wait=True)

    assert aid in market.started
    sub = market.submissions[aid]
    assert len(sub) == 1 and len(sub[0]["files"]) == 4
    assert "C1" in sub[0]["deliverableMarkdown"] and "C2" in sub[0]["deliverableMarkdown"]
    agent_msgs = [m for m in market.messages[aid] if m.get("senderAgentId")]
    assert agent_msgs and "2 clips" in agent_msgs[0]["body"]

    # Nothing new: a second poll must not resubmit.
    worker.poll_once(wait=True)
    assert len(market.submissions[aid]) == 1

    first_files = sub[0]["files"]
    market.request_changes(aid, "C2: start a bit earlier please.")
    worker.poll_once(wait=True)
    assert len(market.submissions[aid]) == 2
    v2 = market.submissions[aid][1]
    assert v2["deliverableMarkdown"].startswith("# Clips: version 2")
    assert "What changed" in v2["deliverableMarkdown"]
    kept = set(first_files) & set(v2["files"])
    assert len(v2["files"]) == 4 and len(kept) == 2  # C1's two files are reused untouched

    # Our own confirmation message must not look like another change request.
    worker.poll_once(wait=True)
    assert len(market.submissions[aid]) == 2


def test_hire_without_video_asks_for_one(market, tmp_path):
    s = settings(tmp_path)
    s.market_base_url, s.market_token = market.base, TOKEN
    worker = Worker(s, MarketClient(market.base, TOKEN))
    aid = market.hire("Clips please", "Make me 3 shorts from my podcast")
    worker.poll_once(wait=True)
    assert aid not in market.submissions
    msgs = [m["body"] for m in market.messages[aid] if m.get("senderAgentId")]
    assert msgs and "couldn't find a video" in msgs[0]


def test_overlapping_runs_do_not_double_work(market, tmp_path):
    import time

    s = settings(tmp_path)
    s.market_base_url, s.market_token = market.base, TOKEN
    first = Worker(s, MarketClient(market.base, TOKEN))
    second = Worker(s, MarketClient(market.base, TOKEN))  # e.g. the next Cloud Run Job execution
    aid = market.hire("Clips", "3 clips please https://example.com/v.mp4")
    first.state.put(aid, busy_until=time.time() + 3600)  # first run has claimed it and is still working
    assert second.poll_once(wait=True) == []
    first.state.put(aid, busy_until=time.time() - 1)  # lease ran out (crashed run)
    assert second.decide(market.assignments[aid]) == "new"
