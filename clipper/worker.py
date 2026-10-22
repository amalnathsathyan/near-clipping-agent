"""Polling worker: watches the marketplace for hires and change requests and runs the pipeline.

Lifecycle per assignment (docs.market.near.ai, Self-hosted agents):
  fresh hire (startedAt null)               -> start, confirm in thread, clip, upload, submit
  in_progress with submittedAt set          -> change request: re-make only the named clips, resubmit
  submittedAt null but deliverableUrl set   -> dispute redo: run the original brief again
State lives in SQLite so a restart never double-submits or loses a job.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import signal
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import pipeline, report
from .brief import parse_brief
from .config import Settings
from .market import MarketClient, MarketError, find_attachments
from .media import AUDIO_EXTS, VIDEO_EXTS

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS assignments (
  id TEXT PRIMARY KEY,
  phase TEXT NOT NULL,            -- running | delivered | waiting_input | failed
  job_dir TEXT NOT NULL,
  confirmed INTEGER DEFAULT 0,
  last_msg_key TEXT DEFAULT '',
  version INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  updated_at REAL
);
"""


class State:
    def __init__(self, path: Path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get(self, aid: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM assignments WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

    def put(self, aid: str, **fields) -> None:
        with self.lock:
            cur = self.db.execute("SELECT id FROM assignments WHERE id=?", (aid,)).fetchone()
            fields["updated_at"] = time.time()
            if cur:
                sets = ", ".join(f"{k}=?" for k in fields)
                self.db.execute(f"UPDATE assignments SET {sets} WHERE id=?", (*fields.values(), aid))
            else:
                fields.setdefault("phase", "running")
                fields.setdefault("job_dir", "")
                cols = ", ".join(["id", *fields])
                qs = ", ".join("?" * (len(fields) + 1))
                self.db.execute(f"INSERT INTO assignments ({cols}) VALUES ({qs})", (aid, *fields.values()))
            self.db.commit()


def msg_key(m: dict | None) -> str:
    if not m:
        return ""
    k = m.get("messageId") or m.get("id") or m.get("createdAt")
    return str(k) if k else hashlib.sha1((m.get("body") or "").encode()).hexdigest()[:16]


def is_own(m: dict | None) -> bool:
    return bool(m and m.get("senderAgentId"))


class Worker:
    def __init__(self, settings: Settings, client: MarketClient | None = None):
        self.settings = settings
        self.client = client or MarketClient(settings.market_base_url, settings.market_token)
        self.state = State(settings.db_path)
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.concurrency))
        self.in_flight: set[str] = set()
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    # ------------------------------------------------------------ loop

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, lambda *_: self.stopping.set())
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        log.info("worker polling %s every %ss", self.settings.market_base_url, self.settings.poll_seconds)
        while not self.stopping.is_set():
            try:
                self.poll_once()
                self.cleanup_old_jobs()
            except MarketError as e:
                log.error("poll failed: %s", e)
            except Exception:
                log.exception("poll crashed")
            self.stopping.wait(self.settings.poll_seconds)
        log.info("stopping; waiting for running jobs")
        self.pool.shutdown(wait=True)

    def poll_once(self, wait: bool = False) -> list:
        futures = []
        for row in self.client.assignments():
            a = row.get("assignment") or {}
            aid = a.get("assignmentId")
            if not aid or a.get("status") != "in_progress":
                continue
            with self.lock:
                if aid in self.in_flight:
                    continue
            action = self.decide(row)
            if not action:
                continue
            with self.lock:
                self.in_flight.add(aid)
            futures.append(self.pool.submit(self._run, action, row))
        if wait:
            for f in futures:
                f.result()
        return futures

    def decide(self, row: dict) -> str | None:
        a = row["assignment"]
        aid = a["assignmentId"]
        lm = row.get("latestMessage")
        rec = self.state.get(aid)
        if a.get("submittedAt"):
            if is_own(lm) or not lm:
                return None
            if rec and rec["last_msg_key"] == msg_key(lm):
                return None
            return "revise"
        if a.get("deliverableUrl"):
            return "redo" if not rec or rec["phase"] != "redo_running" else None
        if rec is None or a.get("startedAt") is None:
            return "new"
        if rec["phase"] == "running":
            return "new"  # crashed mid-job: run it again
        if rec["phase"] in ("waiting_input", "failed") and lm and not is_own(lm) and rec["last_msg_key"] != msg_key(lm):
            return "new"
        return None

    def _run(self, action: str, row: dict) -> None:
        aid = row["assignment"]["assignmentId"]
        try:
            if action == "revise":
                self.handle_revision(row)
            else:
                self.handle_new(row, redo=action == "redo")
        except Exception as e:
            log.exception("assignment %s failed", aid)
            self.state.put(aid, phase="failed", error=str(e)[:500], last_msg_key=msg_key(row.get("latestMessage")))
            try:
                self.client.send_message(aid, "Sorry, something went wrong on my side while making your clips. "
                                              "I've logged it and will retry; you can also reply here to nudge me.")
            except Exception:
                pass
        finally:
            with self.lock:
                self.in_flight.discard(aid)

    # ------------------------------------------------------------ fresh hire

    def _job_dir(self, aid: str) -> Path:
        return self.settings.work_dir / "jobs" / aid

    def _thread_text(self, row: dict) -> tuple[str, list[dict]]:
        """All buyer-side text (brief + thread) and every attachment we can see."""
        a, job = row["assignment"], row.get("job") or {}
        parts = [job.get("title") or "", job.get("description") or ""]
        atts = find_attachments(row)
        msgs = []
        try:
            msgs = self.client.messages(a["assignmentId"])
        except MarketError as e:
            log.info("messages read failed: %s", e)
        if a.get("bidId"):
            msgs += self.client.bid_messages(a["bidId"])
        msgs = [m for m in msgs if not m.get("senderAgentId")]
        msgs.sort(key=lambda m: m.get("createdAt") or "")
        parts += [m.get("body") or "" for m in msgs]
        atts += find_attachments(msgs)
        if a.get("jobId") and not atts:
            atts += find_attachments(self.client.job(a["jobId"]))
        return "\n\n".join(p for p in parts if p), atts

    def handle_new(self, row: dict, redo: bool = False) -> None:
        a = row["assignment"]
        aid = a["assignmentId"]
        job_dir = self._job_dir(aid)
        rec = self.state.get(aid)
        self.client.start(aid)
        self.state.put(aid, phase="redo_running" if redo else "running", job_dir=str(job_dir))

        text, atts = self._thread_text(row)
        brief = parse_brief(text)
        media_atts = [x for x in atts if Path(x["filename"]).suffix.lower() in VIDEO_EXTS | AUDIO_EXTS
                      or x["contentType"].startswith(("video/", "audio/"))]
        if media_atts:
            att = media_atts[-1]  # newest upload wins
            dest = job_dir / "upload" / Path(att["filename"]).name
            if not dest.exists():
                self.client.download(att["url"], dest, att.get("sha256"))
            brief.source = str(dest)

        lm_key = msg_key(row.get("latestMessage"))
        if not brief.source:
            self.client.send_message(aid, "Thanks for hiring me! I couldn't find a video in your request. "
                                          "Please attach the video file here or paste a link (YouTube or a "
                                          "direct file link), and I'll start right away.")
            self.state.put(aid, phase="waiting_input", last_msg_key=lm_key)
            return

        if not rec or not rec.get("confirmed"):
            self.client.send_message(aid, report.confirmation(brief))
            self.state.put(aid, confirmed=1)

        try:
            st = pipeline.run_job(brief, job_dir, self.settings, progress=lambda s: log.info("[%s] %s", aid[:8], s))
        except pipeline.JobError as e:
            self.client.send_message(aid, f"I couldn't process this video: {e}")
            self.state.put(aid, phase="waiting_input", last_msg_key=lm_key, error=str(e)[:500])
            return

        files = self._upload_new(st, job_dir)
        md = report.build(st)
        self.client.submit(aid, md, files)
        pipeline.JobState.save(st, job_dir)
        self.state.put(aid, phase="delivered", version=st.version, last_msg_key=self._latest_key(aid, lm_key))
        log.info("[%s] delivered %d clips", aid[:8], len(st.clips))

    # ------------------------------------------------------------ change request

    def handle_revision(self, row: dict) -> None:
        a = row["assignment"]
        aid = a["assignmentId"]
        lm = row.get("latestMessage") or {}
        text = lm.get("body") or ""
        if lm.get("truncated"):
            msgs = [m for m in self.client.messages(aid) if not m.get("senderAgentId")]
            if msgs:
                text = max(msgs, key=lambda m: m.get("createdAt") or "").get("body") or text
        job_dir = self._job_dir(aid)
        if not (job_dir / "state.json").exists():
            # We have no record of the first delivery (new machine?): treat as a fresh run.
            return self.handle_new(row)
        st, summary, req = pipeline.revise_job(job_dir, text, self.settings,
                                               progress=lambda s: log.info("[%s] %s", aid[:8], s))
        key = msg_key(lm)
        if not summary:
            ids = ", ".join(c.id for c in st.clips)
            hint = ""
            if req.unknown_ids:
                hint = f" I don't have {', '.join(req.unknown_ids)}; this delivery has {ids}."
            elif req.unmatched:
                hint = f" I see you mentioned {', '.join(req.unmatched)} but couldn't tell what to change."
            self.client.send_message(aid, "Happy to change clips." + hint + " Please tell me which clip IDs "
                                          f"({ids}) and what to change, for example \"C2: start earlier\", \"C4: "
                                          "replace with another moment\" or \"C1: end at 14:05\".")
            self.state.put(aid, last_msg_key=key)
            return
        self.client.send_message(aid, "Got it. Changing only " + ", ".join(s.split(':')[0] for s in summary)
                                 + "; everything else stays as delivered.")
        files = self._upload_new(st, job_dir)
        md = report.build(st, changes=summary)
        self.client.submit(aid, md, files)
        st.save(job_dir)
        self.state.put(aid, phase="delivered", version=st.version, last_msg_key=self._latest_key(aid, key))
        log.info("[%s] resubmitted v%d: %s", aid[:8], st.version, summary)

    # ------------------------------------------------------------ helpers

    def cleanup_old_jobs(self) -> None:
        """Delete job folders untouched for KEEP_DAYS (past any review or change window)."""
        root = self.settings.work_dir / "jobs"
        if not root.exists():
            return
        cutoff = time.time() - self.settings.keep_days * 86400
        for d in root.iterdir():
            with self.lock:
                busy = d.name in self.in_flight
            if d.is_dir() and not busy and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)

    def _upload_new(self, st: "pipeline.JobState", job_dir: Path) -> list[dict]:
        """Upload renders that have no marketplace URL yet; reuse the rest. Returns every file of this version."""
        files = []
        for r in st.renders:
            if not r.ok:
                continue
            if not r.url:
                up = self.client.upload(Path(r.path))
                r.url, r.file_id = up["fileUrl"], up["fileId"]
                st.save(job_dir)
            files.append({"fileId": r.file_id, "fileUrl": r.url})
        return files

    def _latest_key(self, aid: str, fallback: str) -> str:
        """Key of the newest buyer message, so our own messages never look like a change request."""
        try:
            msgs = [m for m in self.client.messages(aid) if not m.get("senderAgentId")]
            if msgs:
                return msg_key(max(msgs, key=lambda m: m.get("createdAt") or ""))
        except MarketError:
            pass
        return fallback
