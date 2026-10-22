"""The Markdown deliverable: one section per clip with its reason, signal and files."""

from __future__ import annotations

from .boundaries import fmt_ts
from .brief import describe


def _signal_label(sig: str) -> str:
    return "YouTube Most replayed" if sig == "most_replayed" else "Transcript analysis"


def build(st, file_links: dict[tuple[str, str], str] | None = None, changes: list[str] | None = None) -> str:
    """st is a pipeline.JobState. file_links maps (clip_id, fmt) -> URL (falls back to file names)."""
    links = file_links or {}
    b = st.brief
    out: list[str] = []
    if changes:
        out.append(f"# Clips: version {st.version}\n")
        out.append("## What changed\n")
        out += [f"- {c}" for c in changes]
        out.append("\nEvery other clip is unchanged and listed again below with the same files.\n")
    else:
        out.append("# Your clips\n")

    src = f"[{st.title}]({st.source_url})" if st.source_url else st.title
    made = sum(1 for r in st.renders if r.ok)
    out.append(f"**Source:** {src} ({fmt_ts(st.duration)})  ")
    out.append(f"**Delivered:** {len(st.clips)} clips, {made} files ({describe(b)})  ")
    if st.has_heatmap:
        n_heat = sum(1 for c in st.clips if c.signal == "most_replayed")
        out.append(f"**How moments were chosen:** this video has YouTube's public \"Most replayed\" data. "
                   f"{n_heat} of {len(st.clips)} clips sit on replay peaks; the rest were chosen from the "
                   f"transcript where replay levels were flat.")
    else:
        out.append("**How moments were chosen:** no retention or \"Most replayed\" data was available for this "
                   "video, so every clip was chosen by analysing the full timestamped transcript (hook in the "
                   "first seconds, a complete thought with a payoff, emotion or surprise, quotable lines).")
    out.append("")
    for note in st.notes:
        out.append(f"> {note}\n")

    out.append("| Clip | Source time | Length | Signal | Title |")
    out.append("|---|---|---|---|---|")
    for c in st.clips:
        out.append(f"| {c.id} | {fmt_ts(c.start)}-{fmt_ts(c.end)} | {c.duration:.0f} s | {_signal_label(c.signal)} | {c.title} |")
    out.append("")

    for c in st.clips:
        out.append(f"## {c.id}: {c.title}\n")
        out.append(f"**Source time:** {fmt_ts(c.start)} to {fmt_ts(c.end)} ({c.duration:.1f} s)  ")
        out.append(f"**Why this moment:** {c.reason}  ")
        out.append(f"**Key line:** \"{c.quote}\"  ")
        out.append(f"**Signal:** {_signal_label(c.signal)}: {c.signal_detail}  ")
        files = []
        for r in st.renders:
            if r.clip_id != c.id:
                continue
            if r.ok:
                url = links.get((c.id, r.fmt)) or r.url
                name = r.path.rsplit("/", 1)[-1] if r.path else f"{c.id}_{r.fmt}"
                files.append(f"[{r.fmt} ({name})]({url})" if url else f"{r.fmt}: {name}")
            else:
                files.append(f"{r.fmt}: **not produced**, {r.error}")
        out.append(f"**Files:** {' · '.join(files) if files else 'none'}\n")

    failed = [r for r in st.renders if not r.ok]
    if failed:
        out.append("## Formats that could not be produced\n")
        for r in failed:
            out.append(f"- {r.clip_id} {r.fmt}: {r.error}")
        out.append("")

    out.append("## Need changes?\n")
    out.append("Reply with the clip IDs and what to change, for example: \"C2: start earlier\", \"C4: replace "
               "with a different moment\", \"C1: end at 14:05\", \"bigger captions on C3\". Only the clips you name "
               "are re-made; the rest stay exactly as delivered.")
    return "\n".join(out) + "\n"


def confirmation(brief, title_hint: str = "") -> str:
    parts = [f"Thanks! I'm on it. Here's what I'll make: {describe(brief)}."]
    if brief.source:
        parts.append(f"Source: {brief.source}")
    if brief.defaults_used:
        parts.append(f"You didn't specify some details, so I'm using my defaults: {', '.join(brief.defaults_used)}.")
    if brief.unsupported_formats:
        parts.append(f"I can't render {', '.join(brief.unsupported_formats)}; I'll make the others and note "
                     f"this in the report. Supported: 9:16, 1:1, 4:5, 16:9.")
    parts.append("I'll use YouTube's \"Most replayed\" data if the video has it, otherwise transcript analysis, "
                 "and every clip comes with the reason it was picked. If you want something different, reply "
                 "here; otherwise I'll go ahead with the above.")
    return "\n\n".join(parts)
