import asyncio
import json

from app.services import ai_providers as ai
from app.services.subtitle_qc import find_overloaded, pick_shorter
from app.services.subtitle_service import Segment


def test_find_overloaded_by_chars_per_second():
    segs = [Segment(0, 0, 1000, "x", "Xin chào"),                                   # 7 ký tự / 1s = 7 cps: ổn
            Segment(1, 2000, 3000, "y", "Đây là một câu dịch rất dài dòng và khó đọc kịp"),   # ~38 cps
            Segment(2, 4000, 6000, "z", None)]                                      # chưa dịch: bỏ qua
    over = find_overloaded(segs, 20)
    assert [i for i, _ in over] == [1] and 10 <= over[0][1] <= 30


def test_pick_shorter_rejects_longer_or_empty():
    assert pick_shorter("một hai ba bốn", "một hai") == "một hai"
    assert pick_shorter("ngắn", "dài hơn nhiều") is None and pick_shorter("abc", "") is None and pick_shorter("abc", None) is None


def test_style_prompt():
    assert ai.style_prompt("auto", "vi") == "" and ai.style_prompt(None, "vi") == ""
    assert "sư huynh" in ai.style_prompt("ancient", "vi") and "tôi - anh/chị/em" in ai.style_prompt("modern", "vi")
    assert "archaic" in ai.style_prompt("ancient", "en") and "sư huynh" not in ai.style_prompt("ancient", "en")


def test_translate_all_puts_style_and_glossary_in_prompt():
    seen = {}

    def fake(ai_, system, user):
        seen["system"] = system
        return json.dumps(["ok"] * len(json.loads(user)))

    orig = ai._complete_sync
    ai._complete_sync = fake
    try:
        asyncio.run(ai.LLMTranslator(ai.UserAI("openai", "m", "k")).translate_all(
            ["a"], "vi", "zh", glossary=[("张三", "Trương Tam")], style="ancient"))
    finally:
        ai._complete_sync = orig
    assert "sư huynh" in seen["system"] and "Trương Tam" in seen["system"]


def test_shorten_applies_only_valid_shorter_lines():
    def fake(ai_, system, user):
        n = len(json.loads(user))
        return json.dumps(["ngắn gọn", "DÀI HƠN CẢ BẢN GỐC RẤT NHIỀU LẦN"][:n])

    orig = ai._complete_sync
    ai._complete_sync = fake
    try:
        out = asyncio.run(ai.LLMTranslator(ai.UserAI("openai", "m", "k")).shorten(
            [("một câu rất là dài dòng", 10), ("câu ngắn", 5)], "vi"))
    finally:
        ai._complete_sync = orig
    assert out == ["ngắn gọn", None]
