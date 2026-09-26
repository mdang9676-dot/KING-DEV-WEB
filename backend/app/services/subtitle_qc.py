"""Kiểm tra chất lượng phụ đề: tốc độ đọc (ký tự/giây). Dòng quá nhanh -> nhờ AI rút gọn cho dễ đọc và vừa khớp lồng tiếng."""
import re


def find_overloaded(segments, max_cps: float = 20.0) -> list[tuple[int, int]]:
    """Trả về [(chỉ số dòng, số ký tự tối đa nên có)] cho các dòng DỊCH có tốc độ đọc vượt max_cps (ký tự/giây)."""
    out = []
    for i, s in enumerate(segments):
        t = (s.translated_text or "").strip()
        if not t:
            continue
        dur = max(s.end_ms - s.start_ms, 300) / 1000
        if len(re.sub(r"\s", "", t)) / dur > max_cps:
            out.append((i, max(4, int(max_cps * dur * 1.15))))     # 1.15: cộng khoảng trắng
    return out


def pick_shorter(old: str, new: str | None) -> str | None:
    """Chỉ nhận bản rút gọn nếu thật sự ngắn hơn và không rỗng (tránh AI trả lại bản dài hơn/rỗng)."""
    new = (new or "").strip()
    return new if new and len(new) < len(old.strip()) else None
