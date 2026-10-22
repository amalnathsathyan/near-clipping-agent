"""Synthetic talking-head stand-in for tests: espeak-ng speech over a moving test pattern.

Writes <name>.mp4 plus <name>.words.json (word timings), which the "sidecar" transcriber reads,
so the whole pipeline can run offline without any API key.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import wave
from pathlib import Path

SCRIPT = [
    "Welcome back to the show.",
    "Today we are talking about how small teams ship products faster than big companies.",
    "Here's the thing nobody tells you about startups.",
    "The biggest mistake founders make is building for six months before talking to a single customer.",
    "I did exactly that with my first company, and it cost me two years.",
    "So what changed the second time?",
    "We shipped something ugly in two weeks and put it in front of ten real users.",
    "Seven of them hated it, and that was the most useful feedback we ever got.",
    "Because the three who loved it told us exactly what to build next.",
    "Let me give you a concrete example.",
    "We had a feature that we thought was the core of the product.",
    "Nobody used it.",
    "Not one person in the first month.",
    "Meanwhile a tiny export button we added on a Friday became the reason people paid us.",
    "Why does this happen so often?",
    "Honestly, I think founders fall in love with their solution instead of the problem.",
    "The fix is simple but uncomfortable.",
    "You have to watch people use your product without helping them.",
    "It is painful, and it is the fastest way to learn.",
    "Now let's talk about money, because this is where most teams get it wrong.",
    "Raising a big round too early actually slows you down.",
    "You hire too fast, you build too much, and you stop listening.",
    "The best companies I know stayed small until it hurt.",
    "They said no to almost everything.",
    "Here's a rule that changed my life.",
    "If a feature does not help a customer this week, it waits.",
    "That one rule cut our roadmap in half and doubled our revenue in a year.",
    "People think that sounds crazy, but the numbers do not lie.",
    "Okay, one last thing before we wrap up.",
    "What would I tell my younger self?",
    "Talk to customers every single day, and ship before you feel ready.",
    "Everything else is noise.",
    "Thanks for listening, and see you next week.",
]


def have_tools() -> bool:
    return all(shutil.which(t) for t in ("ffmpeg", "espeak-ng"))


def make_fixture(out_dir: Path, name: str = "talk", repeat: int = 1, size: str = "1280x720",
                 seed: int = 7) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{name}.mp4"
    words_path = out_dir / f"{name}.words.json"
    if video.exists() and words_path.exists():
        return video
    rnd = random.Random(seed)
    tmp = out_dir / f"_{name}_parts"
    tmp.mkdir(exist_ok=True)
    parts, words, t = [], [], 0.0
    lines = SCRIPT * repeat
    for i, line in enumerate(lines):
        wav = tmp / f"s{i:03d}.wav"
        subprocess.run(["espeak-ng", "-s", "165", "-w", str(wav), line], check=True, capture_output=True)
        # espeak pads a little silence at both ends; trim so word timing estimates stay tight.
        trimmed = tmp / f"t{i:03d}.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af",
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                        "-ar", "22050", "-ac", "1", str(trimmed)], check=True)
        with wave.open(str(trimmed)) as w:
            dur = w.getnframes() / w.getframerate()
        toks = line.split()
        weights = [len(x.strip(".,?!")) + 2 for x in toks]
        total = sum(weights)
        cur = t
        for tok, wt in zip(toks, weights):
            span = dur * wt / total
            words.append({"text": tok, "start": round(cur + 0.02, 3), "end": round(cur + span - 0.04, 3)})
            cur += span
        gap = rnd.uniform(0.45, 0.9)
        sil = tmp / f"g{i:03d}.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"anullsrc=r=22050:cl=mono", "-t", f"{gap:.3f}", str(sil)], check=True)
        parts += [trimmed, sil]
        t += dur + gap
    listing = tmp / "list.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    audio = tmp / "all.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(audio)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                    "-i", str(audio), "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                    "-c:a", "aac", "-b:a", "96k", "-pix_fmt", "yuv420p", str(video)], check=True)
    words_path.write_text(json.dumps(words))
    shutil.rmtree(tmp, ignore_errors=True)
    return video


if __name__ == "__main__":
    import sys

    print(make_fixture(Path(sys.argv[1] if len(sys.argv) > 1 else "work/fixtures"),
                       repeat=int(sys.argv[2]) if len(sys.argv) > 2 else 1))
