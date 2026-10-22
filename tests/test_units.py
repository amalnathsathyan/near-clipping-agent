from clipper.boundaries import build_sentences, snap, words_cut
from clipper.brief import parse_brief
from clipper.captions import build_ass, chunk_words
from clipper.models import HeatPoint, Word
from clipper.revision import parse_rules
from clipper.selection import heat_peaks


def words_from(text: str, start: float = 0.0, gap: float = 0.6) -> list[Word]:
    out, t = [], start
    for sent in text.split("|"):
        for tok in sent.split():
            out.append(Word(tok, round(t, 2), round(t + 0.3, 2)))
            t += 0.35
        t += gap
    return out


def test_brief_test_scenarios():
    b = parse_brief("45-minute YouTube video https://youtu.be/abc. 5 clips, vertical, use retention data.")
    assert (b.source, b.clips, b.formats, b.captions) == ("https://youtu.be/abc", 5, ["9:16"], True)
    b = parse_brief("20-min uploaded file. 3 clips, vertical and square. No platform data.")
    assert b.clips == 3 and b.formats == ["9:16", "1:1"] and b.source is None
    b = parse_brief("60-minute podcast, 4 clips, vertical, captions please. 30-45 seconds each")
    assert b.clips == 4 and b.captions and (b.min_len, b.max_len) == (30, 45)


def test_brief_unsupported_and_no_captions():
    b = parse_brief("two clips in 21:9 format, no captions https://example.com/v.mp4")
    assert b.clips == 2 and b.captions is False and "21:9" in b.unsupported_formats
    assert b.source == "https://example.com/v.mp4"


def test_sentences_and_snap_never_cut_words():
    ws = words_from("Hello there friend.|This is the second sentence.|And a third one!")
    sents = build_sentences(ws)
    assert [s.text for s in sents] == ["Hello there friend.", "This is the second sentence.", "And a third one!"]
    start, end = snap(ws, sents, 1, 1, media_end=100)
    assert ws[2].end <= start <= ws[3].start
    assert ws[7].end <= end <= ws[8].start
    assert not words_cut(ws, start) and not words_cut(ws, end)


def test_long_unpunctuated_speech_is_split():
    ws = words_from(" ".join(["word"] * 120))
    ws[60].end += 0.0
    sents = build_sentences(ws)
    assert len(sents) > 1
    assert all(s.end - s.start <= 26 for s in sents)


def test_caption_chunks_short_lines():
    ws = words_from("This is a fairly long sentence that should be broken into short readable lines.")
    chunks = chunk_words(ws)
    assert all(1 <= len(c) <= 4 for c in chunks)
    assert all(sum(len(w.text) + 1 for w in c) <= 26 for c in chunks)
    ass = build_ass(ws, 0, 10, 1080, 1920, "DejaVu Sans")
    assert "PlayResY: 1920" in ass and ass.count("Dialogue:") >= len(ws)


def test_revision_parser_only_named_clips():
    ids = ["C1", "C2", "C3", "C4", "C5"]
    r = parse_rules("C3 is great but C4 feels random. C2: start 5 seconds earlier.", ids)
    assert {(e.clip, e.action) for e in r.edits} == {("C4", "replace"), ("C2", "extend_start")}
    assert next(e for e in r.edits if e.clip == "C2").seconds == 5
    r = parse_rules("Use 3:10-3:55 for clip 1", ids)
    assert (r.edits[0].clip, r.edits[0].start, r.edits[0].end) == ("C1", 190, 235)
    r = parse_rules("C9 start earlier", ids)
    assert not r.edits and r.unknown_ids == ["C9"]


def test_heat_peaks_above_baseline():
    heat = [HeatPoint(i * 10, i * 10 + 10, 0.2) for i in range(100)]
    heat[40].value = 0.9
    heat[70].value = 0.6
    peaks = heat_peaks(heat)
    assert [p.start for p in peaks] == [400, 700]
