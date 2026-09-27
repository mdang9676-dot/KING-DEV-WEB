from app.services.subtitle_cleanup import filter_junk, is_junk
from app.services.subtitle_service import Segment
from app.services.vi_tts_normalize import normalize_vi, num_to_vi


def test_number_reading():
    exp = {0: "không", 5: "năm", 10: "mười", 11: "mười một", 14: "mười bốn", 15: "mười lăm", 21: "hai mươi mốt",
           24: "hai mươi tư", 25: "hai mươi lăm", 100: "một trăm", 101: "một trăm linh một", 105: "một trăm linh năm",
           110: "một trăm mười", 115: "một trăm mười lăm", 1000: "một nghìn", 1005: "một nghìn không trăm linh năm",
           2024: "hai nghìn không trăm hai mươi tư", 100000: "một trăm nghìn", 1000000: "một triệu",
           1500000: "một triệu năm trăm nghìn", 1001000: "một triệu không trăm linh một nghìn", 2000000000: "hai tỷ"}
    for n, s in exp.items():
        assert num_to_vi(n) == s, (n, num_to_vi(n), s)


def test_text_normalization():
    cases = {
        "Giá 50% và $5": "Giá năm mươi phần trăm và năm đô la",
        "lúc 3:15": "lúc ba giờ mười lăm phút",
        "hẹn 12:00": "hẹn mười hai giờ",
        "8g30 sáng": "tám giờ ba mươi phút sáng",
        "3g15p": "ba giờ mười lăm phút",
        "ngày 12/03/2024": "ngày ngày mười hai tháng ba năm hai nghìn không trăm hai mươi tư",
        "cách 3,5 km": "cách ba phẩy năm ki lô mét",
        "trả 1.000.000 đồng": "trả một triệu đồng",
        "từ 5-10 người": "từ năm đến mười người",
        "gọi 0912345678": "gọi không chín một hai ba bốn năm sáu bảy tám",
        "nóng 35°C": "nóng ba mươi lăm độ xê",
        "2+3=5": "hai cộng ba bằng năm",
        "1/2 cốc": "một phần hai cốc",
        "♪ la la ♪": "la la",
        "mail ken@abc": "mail ken a còng abc",
    }
    for src, want in cases.items():
        assert normalize_vi(src) == want, (src, normalize_vi(src), want)


def test_does_not_mangle_plain_text_or_words():
    assert normalize_vi("Xin chào các bạn") == "Xin chào các bạn"
    assert normalize_vi("COVID-19 lan nhanh") == "COVID-mười chín lan nhanh" or "COVID" in normalize_vi("COVID-19 lan nhanh")
    assert normalize_vi("Mạng 5G rất nhanh") == "Mạng năm G rất nhanh"        # không bị coi là "5 giờ"
    assert normalize_vi("090-123-4567") .count("đến") == 0                      # số điện thoại có gạch không thành "khoảng"
    assert normalize_vi("") == ""


def test_junk_filter():
    assert is_junk("|", 300) and is_junk("...", 2000) and is_junk("好", 400) and is_junk("------", 3000)
    assert not is_junk("好", 2000) and not is_junk("Xin chào", 500) and not is_junk("2024", 800)
    segs = [Segment(0, 0, 300, "|"), Segment(1, 1000, 3000, "Xin chào"), Segment(2, 3000, 3200, "a")]
    out = filter_junk(segs)
    assert [s.text for s in out] == ["Xin chào"] and out[0].index == 0
