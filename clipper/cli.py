"""Command line: run the pipeline locally, revise a job, run the marketplace worker, or check setup."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import config, report


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args) -> int:
    from .brief import describe, parse_brief
    from .pipeline import JobError, run_job

    s = config.load()
    text = args.brief or ""
    b = parse_brief(f"{text}\n{args.source}")
    b.source = args.source
    if args.clips:
        b.clips = args.clips
    if args.formats:
        b.formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    if args.no_captions:
        b.captions = False
    if args.min_len:
        b.min_len = args.min_len
    if args.max_len:
        b.max_len = args.max_len
    out = Path(args.out or (s.work_dir / "jobs" / f"local-{int(time.time())}")).resolve()
    print(f"Brief: {describe(b)}\nJob folder: {out}")
    try:
        st = run_job(b, out, s, progress=lambda m: print(f"  … {m}"))
    except JobError as e:
        print(f"Job failed: {e}", file=sys.stderr)
        return 2
    md = report.build(st)
    (out / "report.md").write_text(md)
    _print_summary(st, out)
    return 0 if all(c["ok"] for c in st.checks) else 1


def cmd_revise(args) -> int:
    from .pipeline import revise_job

    s = config.load()
    job = Path(args.job_dir).resolve()
    st, summary, req = revise_job(job, args.request, s, progress=lambda m: print(f"  … {m}"))
    if not summary:
        print("No change understood. Name clip IDs, e.g. \"C2: start earlier\".")
        if req.unknown_ids:
            print("Unknown clip IDs:", ", ".join(req.unknown_ids))
        return 2
    (job / f"report_v{st.version}.md").write_text(report.build(st, changes=summary))
    print("Changed:\n  " + "\n  ".join(summary))
    _print_summary(st, job)
    return 0


def _print_summary(st, out: Path) -> None:
    print(f"\nTranscriber: {st.transcriber}  Selector: {st.selector}  Heatmap: {'yes' if st.has_heatmap else 'no'}")
    print(f"Timings: {json.dumps(st.timings)}")
    for c in st.clips:
        print(f"  {c.id} {c.start:8.2f}-{c.end:8.2f} ({c.duration:4.1f}s) [{c.signal}] {c.title}")
    bad = [c for c in st.checks if not c["ok"]]
    print(f"Checks: {len(st.checks) - len(bad)}/{len(st.checks)} passed")
    for c in bad:
        print(f"  FAIL {c['check']}: {c['detail']}")
    print(f"Report: {out / ('report.md' if st.version == 1 else f'report_v{st.version}.md')}")


def cmd_worker(args) -> int:
    from .worker import Worker

    s = config.load()
    if args.once:  # one poll, finish every job it found, then exit (cron / Cloud Run Job mode)
        w = Worker(s)
        w.poll_once(wait=True)
        w.cleanup_old_jobs()
        return 0
    Worker(s).run_forever()
    return 0


def cmd_brief(args) -> int:
    from .brief import parse_brief

    print(json.dumps(asdict(parse_brief(args.text)), indent=2))
    return 0


def cmd_probe(args) -> int:
    """Check every dependency and credential without spending anything."""
    import requests

    from .media import ensure_tools

    s = config.load()
    ok = True

    def line(name: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"[{'ok' if good else '!!'}] {name}{': ' + detail if detail else ''}")

    missing = ensure_tools()
    line("ffmpeg/ffprobe", not missing, "missing " + ", ".join(missing) if missing else "found")
    try:
        import yt_dlp

        line("yt-dlp", True, yt_dlp.version.__version__)
    except ImportError:
        line("yt-dlp", False, "pip install yt-dlp")
    def get(url: str, **kw):
        try:
            r = requests.get(url, timeout=20, **kw)
            return r.ok, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            return False, f"network error: {str(e)[:160]}"

    if s.groq_api_key:
        good, detail = get("https://api.groq.com/openai/v1/models",
                           headers={"Authorization": f"Bearer {s.groq_api_key}"})
        line("GROQ_API_KEY", good, detail)
    else:
        line("GROQ_API_KEY", False, "not set (needed for transcription unless faster-whisper is installed)")
    if s.gemini_api_key:
        good, detail = get(f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}",
                           headers={"x-goog-api-key": s.gemini_api_key})
        line("GEMINI_API_KEY", good, f"{detail} for {s.gemini_model}")
    else:
        print("[--] GEMINI_API_KEY: not set (optional; Groq LLM or the built-in heuristic is used)")
    if s.market_token:
        from .market import MarketClient, MarketError

        try:
            c = MarketClient(s.market_base_url, s.market_token)
            rows = c.assignments()
            line("MARKET_AGENT_TOKEN", True, f"{len(rows)} in-progress assignment(s)")
            if args.upload:
                up = c.upload(Path(args.upload))
                line("file upload", True, json.dumps({k: up[k] for k in ("fileId", "fileUrl")}))
                print("    raw complete response:", json.dumps(up.get("raw"), default=str)[:500])
        except MarketError as e:
            line("MARKET_AGENT_TOKEN", False, str(e)[:300])
    else:
        line("MARKET_AGENT_TOKEN", False, "not set (needed for the marketplace worker)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipper", description="NEAR Agent Market video clipping agent")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="clip a video locally")
    r.add_argument("source", help="YouTube/URL or a local file")
    r.add_argument("--clips", type=int)
    r.add_argument("--formats", help="comma list: 9:16,1:1,4:5,16:9")
    r.add_argument("--no-captions", action="store_true")
    r.add_argument("--min-len", type=float)
    r.add_argument("--max-len", type=float)
    r.add_argument("--brief", help="free-text brief, parsed like a buyer request")
    r.add_argument("--out", help="job folder")
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("revise", help="apply a change request to a finished local job")
    v.add_argument("job_dir")
    v.add_argument("request", help='e.g. "C2: start earlier. Replace C4."')
    v.set_defaults(fn=cmd_revise)

    w = sub.add_parser("worker", help="poll NEAR Agent Market and fulfil hires")
    w.add_argument("--once", action="store_true", help="one poll, wait for jobs, exit")
    w.set_defaults(fn=cmd_worker)

    b = sub.add_parser("brief", help="show how a request text is parsed")
    b.add_argument("text")
    b.set_defaults(fn=cmd_brief)

    pr = sub.add_parser("probe", help="check tools, API keys and the marketplace token")
    pr.add_argument("--upload", help="also test a marketplace file upload with this file")
    pr.set_defaults(fn=cmd_probe)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
