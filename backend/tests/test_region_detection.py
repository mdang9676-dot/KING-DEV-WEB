import random

from app.services.region_detection import Box, find_subtitle_region

W, H = 1280, 720

SENTENCES = [
    "Hello everyone welcome back", "Today we are going to cook", "First add two cups of flour",
    "Then mix it very well", "Now let it rest for ten minutes", "Look how nice this turned out",
    "Thanks for watching see you", "Do not forget to subscribe", "Next we prepare the sauce",
    "Add salt and a little pepper", "Stir gently and wait", "Almost done just a moment",
]


def words(text, x_start, y, h=34, char_w=17, conf=88):
    boxes, x = [], x_start
    for w in text.split():
        bw = len(w) * char_w
        boxes.append(Box(x, y, bw, h, conf, w))
        x += bw + 12
    return boxes


def line_width(t):
    return sum(len(w) * 17 for w in t.split()) + 12 * (len(t.split()) - 1)


def test_picks_subtitle_band_not_logo_or_title():
    random.seed(7)
    frames = []
    for i in range(24):
        boxes = words("STUDIO TV", 1080, 30, h=28)  # logo tĩnh ở góc
        if i % 4 != 3:
            t = SENTENCES[i % len(SENTENCES)]
            boxes += words(t, (W - line_width(t)) // 2 + random.randint(-3, 3), 615 + random.randint(-3, 3))
        if i in (5, 6):
            boxes += words("Chapter One", 480, 300, h=60)  # tiêu đề thoáng qua
        boxes.append(Box(100, 400, 50, 20, 15, "noise"))  # nhiễu conf thấp
        frames.append(boxes)

    r = find_subtitle_region(frames, W, H)
    assert not r.fallback
    assert 560 <= r.y <= 640 and r.y + r.height <= 700
    assert r.x < 400 and r.x + r.width > 880


def test_two_line_subtitle_is_fully_covered():
    frames = []
    for i in range(20):
        a, b = SENTENCES[i % 12], SENTENCES[(i + 5) % 12]
        frames.append(words(a, (W - line_width(a)) // 2, 590) + words(b, (W - line_width(b)) // 2, 640))
    r = find_subtitle_region(frames, W, H)
    assert r.y <= 590 and r.y + r.height >= 674


def test_static_banner_only_falls_back():
    frames = [words("SUBSCRIBE NOW", 500, 640) for _ in range(20)]
    r = find_subtitle_region(frames, W, H)
    assert r.fallback
    assert "đứng yên" in r.reason


def test_no_text_falls_back_to_bottom_default():
    r = find_subtitle_region([[] for _ in range(10)], W, H)
    assert r.fallback and r.y > H * 0.6


def test_too_few_frames_falls_back():
    frames = [words("only twice", 500, 640), words("only twice", 500, 640)] + [[] for _ in range(20)]
    assert find_subtitle_region(frames, W, H).fallback


def test_top_subtitle_still_detected_when_alone():
    frames = [words(SENTENCES[i % 12], (W - line_width(SENTENCES[i % 12])) // 2, 60) for i in range(20)]
    r = find_subtitle_region(frames, W, H)
    assert not r.fallback and r.y < 100
