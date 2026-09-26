"""
Chuẩn hoá văn bản TIẾNG VIỆT trước khi đưa vào giọng đọc (TTS): số, tiền, %, giờ, ngày, khoảng, đơn vị -> chữ.
Nhiều giọng đọc đọc sai "3g15p", "$5", "12/03/2024", "1.000.000" -> chuyển sang lời nói trước sẽ tự nhiên hơn.
Hàm thuần Python, không phụ thuộc thư viện ngoài.
"""
import re

_D = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_SCALE = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]


def _read3(n: int, full: bool) -> str:
    h, t, u = n // 100, n // 10 % 10, n % 10
    out = []
    if h or full:
        out.append(f"{_D[h]} trăm")
    if t > 1:
        out.append(f"{_D[t]} mươi")
        out.append({1: "mốt", 4: "tư", 5: "lăm"}.get(u, _D[u]) if u else "")
    elif t == 1:
        out.append("mười")
        if u:
            out.append("lăm" if u == 5 else _D[u])
    elif u:
        if h or full:
            out.append("linh")
        out.append(_D[u])
    return " ".join(x for x in out if x)


def digits_vi(s: str) -> str:
    return " ".join(_D[int(c)] for c in s if c.isdigit())


def num_to_vi(n: int) -> str:
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + num_to_vi(-n)
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    if len(groups) > len(_SCALE):
        return digits_vi(str(sum(g * 1000 ** i for i, g in enumerate(groups))))
    top, parts = len(groups) - 1, []
    for i in range(top, -1, -1):
        if groups[i]:
            parts.append(" ".join(x for x in (_read3(groups[i], i != top), _SCALE[i]) if x))
    return " ".join(parts)


_THOUSANDS = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def spell_number(tok: str) -> str:
    """Đọc một số dạng văn bản: 1.000.000 | 1,000 | 3,5 | 007 | 0912345678 | 2024."""
    if _THOUSANDS.match(tok):
        return num_to_vi(int(re.sub(r"[.,]", "", tok)))
    m = re.match(r"^(\d+)[.,](\d+)$", tok)
    if m:
        ip, fp = m.groups()
        frac = digits_vi(fp) if fp[0] == "0" or len(fp) > 3 else num_to_vi(int(fp))
        return f"{spell_number(ip)} phẩy {frac}"
    if len(tok) > 1 and (tok[0] == "0" or len(tok) >= 10):          # số điện thoại, mã số: đọc từng chữ số
        return digits_vi(tok)
    return num_to_vi(int(tok))


_NUM = r"\d+(?:[.,]\d+)*"
_CUR = {"$": "đô la", "€": "ơ rô", "¥": "yên", "£": "bảng Anh", "₩": "won"}
_UNITS = {"km/h": "ki lô mét trên giờ", "kg": "ki lô gam", "km": "ki lô mét", "cm": "xen ti mét", "mm": "mi li mét",
          "ml": "mi li lít", "°c": "độ xê", "°f": "độ ép", "m": "mét", "g": "gam", "l": "lít"}


def _time(h: int, m: int, s: int | None = None) -> str:
    out = f"{num_to_vi(h)} giờ"
    if m:
        out += f" {num_to_vi(m)} phút"
    if s:
        out += f" {num_to_vi(s)} giây"
    return out


def normalize_vi(text: str) -> str:
    t = re.sub(r"[♪♫♬★☆■□◆◇●○]+", " ", text or "")
    t = t.replace("@", " a còng ")

    def date(m):
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"ngày {num_to_vi(d)} tháng {num_to_vi(mo)} năm {num_to_vi(y)}"
        return m[0]
    t = re.sub(r"(?<![\d/.\-])(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)", date, t)

    def clock(m):
        h, mi = int(m[1]), int(m[2])
        s = int(m[3]) if m[3] else None
        return _time(h, mi, s) if h <= 23 and mi <= 59 and (s is None or s <= 59) else m[0]
    t = re.sub(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])", clock, t)
    t = re.sub(r"(?<![\w])(\d{1,2})[gh](\d{1,2})(?:p|ph|phút)?(?![\w])",
               lambda m: _time(int(m[1]), int(m[2])) if int(m[1]) <= 23 and int(m[2]) <= 59 else m[0], t)
    t = re.sub(r"(?<![\w])(\d{1,2})h(?![\w])", lambda m: f"{num_to_vi(int(m[1]))} giờ" if int(m[1]) <= 24 else m[0], t)

    t = re.sub(rf"({_NUM})\s*%", lambda m: f"{spell_number(m[1])} phần trăm", t)
    t = re.sub(rf"([$€¥£₩])\s*({_NUM})", lambda m: f"{spell_number(m[2])} {_CUR[m[1]]}", t)
    t = re.sub(rf"({_NUM})\s*([$€¥£₩])", lambda m: f"{spell_number(m[1])} {_CUR[m[2]]}", t)
    t = re.sub(rf"({_NUM})\s*USD\b", lambda m: f"{spell_number(m[1])} đô la Mỹ", t, flags=re.I)
    t = re.sub(rf"({_NUM})\s*(?:VND|VNĐ)\b", lambda m: f"{spell_number(m[1])} đồng", t, flags=re.I)
    t = re.sub(rf"({_NUM})\s*(km/h|kg|km|cm|mm|ml|°C|°F|m|g|l)(?![\w/])",
               lambda m: f"{spell_number(m[1])} {_UNITS[m[2].lower()]}", t)

    t = re.sub(r"(?<!\d-)(?<!\d–)(?<!\d—)\b(\d{1,4})\s*[-–—]\s*(\d{1,4})\b(?![-–—]\d)",
               lambda m: f"{spell_number(m[1])} đến {spell_number(m[2])}", t)
    t = re.sub(r"(?<=\d)\s*\+\s*(?=\d)", " cộng ", t)
    t = re.sub(r"(?<=\d)\s*=\s*(?=\d)", " bằng ", t)
    t = re.sub(r"(\d+)\s*/\s*(\d+)", lambda m: f"{spell_number(m[1])} phần {spell_number(m[2])}", t)
    t = re.sub(_NUM, lambda m: f" {spell_number(m[0])} ", t)        # tách khỏi chữ liền kề ("5G" -> "năm G")
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"\s+([,.;:!?])", r"\1", t).strip()
