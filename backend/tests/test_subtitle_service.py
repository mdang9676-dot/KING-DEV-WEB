from app.services.subtitle_service import (
    Segment, to_srt, to_vtt, to_txt, parse_srt, shift_timing, merge_segments, split_segment,
)


def make_segments():
    return [
        Segment(0, 0, 2000, "Hello world"),
        Segment(1, 2000, 4500, "This is a test"),
    ]


def test_to_srt_format():
    srt = to_srt(make_segments())
    assert "1\n00:00:00,000 --> 00:00:02,000\nHello world" in srt
    assert "2\n00:00:02,000 --> 00:00:04,500\nThis is a test" in srt


def test_to_srt_bilingual():
    segs = [Segment(0, 0, 1000, "Hello", translated_text="Xin chào")]
    srt = to_srt(segs, bilingual=True)
    assert "Xin chào\nHello" in srt


def test_to_vtt_format():
    vtt = to_vtt(make_segments())
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.000" in vtt


def test_to_txt():
    txt = to_txt(make_segments())
    assert txt == "Hello world\nThis is a test"


def test_parse_srt_roundtrip():
    original = make_segments()
    srt_content = to_srt(original)
    parsed = parse_srt(srt_content)
    assert len(parsed) == 2
    assert parsed[0].text == "Hello world"
    assert parsed[0].start_ms == 0
    assert parsed[0].end_ms == 2000
    assert parsed[1].start_ms == 2000
    assert parsed[1].end_ms == 4500


def test_shift_timing():
    segs = make_segments()
    shifted = shift_timing(segs, 500)
    assert shifted[0].start_ms == 500
    assert shifted[0].end_ms == 2500
    # Không cho phép thời gian âm
    shifted_negative = shift_timing(segs, -10000)
    assert shifted_negative[0].start_ms == 0


def test_merge_segments():
    a = Segment(0, 0, 1000, "Hello")
    b = Segment(1, 1000, 2000, "world")
    merged = merge_segments(a, b)
    assert merged.text == "Hello world"
    assert merged.start_ms == 0
    assert merged.end_ms == 2000


def test_split_segment():
    seg = Segment(0, 0, 4000, "one two three four")
    first, second = split_segment(seg, 0.5)
    assert first.start_ms == 0
    assert second.end_ms == 4000
    assert first.end_ms == second.start_ms
    assert first.text + " " + second.text == seg.text
