# NEAR Clipping Agent

A self-hosted **Video Clipping Agent** for [NEAR Agent Market](https://market.near.ai). A buyer hires it with a
long video (YouTube link, file link or an uploaded file). It returns short captioned clips in the formats they
asked for, plus a report that explains why each moment was picked.

- **Moments.** It uses YouTube's public **"Most replayed"** data when the video has it. Otherwise it analyses the
  full timestamped transcript, looking for a hook, a complete thought with a payoff, emotion and quotable lines.
  The report says which signal each clip used.
- **Clean cuts.** Every clip starts and ends on a sentence boundary, inside the pause between words. An automated
  check rejects any cut that lands inside a word.
- **Formats.** It renders 9:16, 1:1, 4:5 and 16:9. A face-aware crop keeps the speaker in frame. When the faces are
  too far apart to crop, it shows the whole frame over a blurred background. Audio-only sources get a waveform
  video. If a requested format can't be made, the report lists it with the reason.
- **Captions.** Burned in from Whisper word timings and sized for phones, with 2 to 4 words per line and the spoken
  word highlighted. They sit above the area that TikTok, Reels and Shorts cover with their own buttons.
- **Changes.** Clips have stable IDs (C1, C2 and so on). A change request such as "C2: start earlier", "replace C4"
  or "C1: end at 14:05" re-renders only the clips named and resubmits the others unchanged.

## How it works

```
marketplace poll ─► start + confirm in thread ─► fetch (yt-dlp + heatmap / buyer upload)
  ─► transcribe (Groq Whisper, word timestamps) ─► sentences + silences
  ─► candidates (whole sentences, sized to the brief) scored by replay heat + content cues
  ─► LLM picks and writes reasons (Gemini Flash, Groq fallback, heuristic without keys)
  ─► snap cuts to pauses ─► face-aware reframe ─► ffmpeg render + ASS captions + loudness
  ─► verify (file count, no mid-word cut, lengths, outputs) ─► upload ─► submit Markdown report
```

All video work runs locally with ffmpeg, so no step has a serverless timeout. The models only receive audio
and transcript text.

| Module | Job |
|---|---|
| `clipper/worker.py` | Polls `/v1/agents/me/assignments`, runs hires, change requests and redos, and stores state in SQLite |
| `clipper/market.py` | REST client: start, messages, file upload (reserve, PUT, complete), submit |
| `clipper/pipeline.py` | Orchestrates a job and per-clip revisions |
| `clipper/selection.py` | Candidate windows, heatmap peaks, LLM and heuristic picking, reasons |
| `clipper/boundaries.py` | Sentence detection, cut snapping, mid-word checks |
| `clipper/captions.py`, `render.py`, `reframe.py` | Captions, ffmpeg rendering, face tracking |
| `clipper/brief.py`, `revision.py`, `report.py` | Brief parsing, change-request parsing, the Markdown deliverable |

## Setup (your own machine)

You need Python 3.11+, ffmpeg and Deno. yt-dlp needs a JavaScript runtime for YouTube.

```bash
# macOS: brew install ffmpeg deno        Ubuntu: sudo apt install ffmpeg && curl -fsSL https://deno.land/install.sh | sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # then fill in the keys (see below)
clipper probe             # checks ffmpeg, yt-dlp, each API key and the marketplace token
```

Or with Docker, which bundles ffmpeg, Deno and fonts:

```bash
cp .env.example .env && docker compose up -d --build && docker compose logs -f
```

## Environment variables

| Variable | Needed? | Where to get it |
|---|---|---|
| `MARKET_AGENT_TOKEN` | Yes, for the worker | market.near.ai: create the agent, choose self-hosted, then copy the `aat_…` API token from the agent's page (it's shown once) |
| `GROQ_API_KEY` | Yes, for transcription | https://console.groq.com/keys (free tier, no card) |
| `GEMINI_API_KEY` | Recommended | https://aistudio.google.com/apikey (free tier, no card). Without it, the Groq LLM picks clips; without either, a built-in heuristic does |
| `YTDLP_COOKIES` | Only if YouTube bot-checks you | Export a `cookies.txt` from a logged-in browser and set its path |
| everything else | No | Defaults are in `.env.example` |

## Commands

```bash
clipper run "https://www.youtube.com/watch?v=…" --clips 5 --formats 9:16          # local run, no marketplace
clipper run talk.mp4 --brief "3 clips, vertical and square, 30-45 seconds"
clipper revise work/jobs/<job> "C2: start earlier. Replace C3."                     # change only named clips
clipper brief "Make 4 vertical clips with captions from https://youtu.be/…"         # see how a request is read
clipper worker                                                                      # poll the marketplace (Ctrl-C to stop)
clipper probe --upload work/jobs/<job>/out/v1/C1_9x16.mp4                           # test a real marketplace upload
```

Each job folder holds `report.md`, `state.json` (plans, checks, timings) and `out/v<N>/` with the clips.

## Tests

```bash
pip install -e ".[dev]" && sudo apt install espeak-ng   # espeak makes a synthetic talking video
pytest -q
```

The tests run fully offline. A synthetic talk is generated with espeak-ng, and its exact word timings stand in
for Whisper. A fake Agent Market server covers the whole hire, deliver and change-request cycle, including the
check that unchanged clips keep their files.

## Notes and limits

- **YouTube from servers.** YouTube often blocks downloads from datacenter IPs, so run the worker on a home
  connection during review. If a link can't be downloaded, the agent asks the buyer to upload the file instead.
- **File upload fields.** The public docs describe the upload as reserve, PUT, complete, but they don't list
  every field name. `market.py` accepts the documented variants. Run `clipper probe --upload <file>` once with the
  real token to confirm before going live.
- **Face tracking.** It uses one crop position per clip, at the median position of the main face. Fast speaker
  switches inside one clip aren't followed yet.
