import functools

from app.services import whisper_service as ws


def raises(fn, *a):
    try:
        fn(*a)
    except ValueError:
        return True
    return False


def test_resolve_model_size():
    assert ws.resolve_model_size("small") == "small" and ws.resolve_model_size("large-v3") == "large-v3"
    assert ws.resolve_model_size(None) in ws.WHISPER_SIZES                      # mặc định trong cấu hình
    assert raises(ws.resolve_model_size, "../../etc/passwd") and raises(ws.resolve_model_size, "huge")


def test_transcribe_loads_the_requested_size_only_once():
    loaded = []

    class FakeModel:
        def transcribe(self, *a, **k):
            class Info:
                language = "en"
            return iter([]), Info()

    orig = ws._get_model
    ws._get_model = functools.lru_cache(maxsize=1)(lambda size: (loaded.append(size), FakeModel())[1])
    try:
        ws.transcribe_audio("x.wav", "en", "small")
        ws.transcribe_audio("x.wav", "en", "small")
        ws.transcribe_audio("x.wav", "en", "tiny")
    finally:
        ws._get_model = orig
    assert loaded == ["small", "tiny"]                                            # cùng cỡ thì dùng lại, đổi cỡ mới nạp lại
