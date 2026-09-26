import shutil
import tempfile
from pathlib import Path

from app.services.ocr_text import (detect_script_lang, frames_similar, merge_ocr_frames, normalize_ocr_text,
                                   script_family, texts_match)


def test_normalize_cjk_and_junk():
    assert normalize_ocr_text("你 好 世 界") == "你好世界"                       # bỏ dấu cách giữa chữ Hán
    assert normalize_ocr_text("Ｈｅｌｌｏ，ｗｏｒｌｄ") == "Hello,world"            # full-width -> thường
    assert normalize_ocr_text("  | -- Xin chào --  \n") == "Xin chào"
    assert normalize_ocr_text("--- | ~~") == "" and normalize_ocr_text("") == ""
    assert normalize_ocr_text("Hello world") == "Hello world"                    # chữ Latin giữ khoảng trắng


def test_fuzzy_match():
    assert texts_match("我今天要去公园玩耍", "我今天要去公园玩要")                   # OCR sai 1 chữ vẫn là cùng câu
    assert texts_match("Hello everyone welcome", "Hello everyone we1come")
    assert not texts_match("我今天要去公园玩耍", "明天我们一起吃饭吧")
    assert not texts_match("Yes", "Yep")                                        # câu ngắn phải giống hệt
    assert not texts_match("", "abc")


def test_merge_survives_ocr_jitter_and_votes_best_text():
    # 1 câu hiện 6 khung, OCR đọc sai chữ ở 2 khung, có 1 khung mất chữ; rồi sang câu khác
    raw = [(0, ""), (500, "我今天要去公园玩耍"), (1000, "我今天要去公园玩要"), (1500, "我今天要去公园玩耍"),
           (2000, ""), (2500, "我今天要去公园玩耍"), (3000, "我今大要去公园玩耍"),
           (3500, "明天我们一起吃饭吧"), (4000, "明天我们一起吃饭吧"), (4500, ""), (5000, ""), (5500, "")]
    segs = merge_ocr_frames(raw, 500)
    assert len(segs) == 2, [(s.start_ms, s.end_ms, s.text) for s in segs]      # KHÔNG bị vỡ thành nhiều đoạn
    assert segs[0].text == "我今天要去公园玩耍"                                   # bỏ phiếu đa số: chọn bản đúng
    assert (segs[0].start_ms, segs[0].end_ms) == (500, 3500)
    assert segs[1].text == "明天我们一起吃饭吧" and segs[1].end_ms == 4500
    assert [s.index for s in segs] == [0, 1]


def test_merge_splits_after_long_gap_and_drops_blips():
    raw = [(0, "Hello there friend"), (500, "Hello there friend"), (1000, ""), (1500, ""), (2000, "Hello there friend"),
           (2500, "x")]
    segs = merge_ocr_frames(raw, 500, min_dur_ms=600)
    assert [(s.start_ms, s.end_ms) for s in segs] == [(0, 1000)]              # 2 khung trống liền nhau => tách đoạn; đoạn 1 khung bị bỏ
    assert merge_ocr_frames([], 500) == []


def test_detect_script_lang():
    assert detect_script_lang("我今天要去公园玩耍吧") == "zh"
    assert detect_script_lang("今日は公園に行きます、ありがとう") == "ja"        # có kana => Nhật
    assert detect_script_lang("오늘 공원에 갑니다 안녕하세요") == "ko"
    assert detect_script_lang("Привет как дела друг") == "ru"
    assert detect_script_lang("สวัสดีครับวันนี้") == "th"
    assert detect_script_lang("Hôm nay chúng ta đi công viên nhé") == "vi"
    assert detect_script_lang("Hello everyone welcome back") == "en"
    assert detect_script_lang("ok") is None                                      # quá ít chữ
    assert script_family("zh") == script_family("ja") == "han" and script_family("en") == "latin" and script_family(None) is None


def test_frames_similar_with_real_images():
    try:
        from PIL import Image
    except ImportError:
        return
    d = Path(tempfile.mkdtemp())
    def img(name, color, noise=0):
        im = Image.new("RGB", (300, 60), color)
        if noise:
            for x in range(0, 300, 3):
                im.putpixel((x, 30), (255, 255, 255))
        im.save(d / name)
        return d / name
    a, b = img("a.png", (40, 40, 40)), img("b.png", (40, 40, 40))
    c, e = img("c.png", (40, 40, 40), noise=1), img("e.png", (200, 30, 30))
    assert frames_similar(a, b)                     # y hệt
    assert not frames_similar(a, e)                 # khác hẳn
    assert not frames_similar(a, d / "khong-co.png")  # lỗi đọc file => coi là khác (không bao giờ bỏ sót chữ)
