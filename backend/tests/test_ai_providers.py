import asyncio
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.services import ai_providers as ai


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    return False


def top(key):
    d = ai.detect_provider(key)
    return [c["provider"] for c in d["candidates"]], d["needs_choice"]


def test_detect_confident_prefixes():
    assert top("sk-ant-api03-" + "a" * 40) == (["anthropic"], False)
    assert top("sk-or-v1-" + "a" * 40) == (["openrouter"], False)
    assert top("sk-proj-" + "A" * 60) == (["openai"], False)
    assert top("AIza" + "B" * 35) == (["gemini"], False)
    assert top("gsk_" + "a" * 40) == (["groq"], False)
    assert top("xai-" + "a" * 40) == (["xai"], False)


def test_detect_ambiguous_asks_user_instead_of_guessing():
    provs, needs = top("sk-" + "a1" * 16)            # sk- + 32 hex: khả năng DeepSeek nhưng không chắc
    assert provs == ["deepseek"] and needs is True
    provs, needs = top("sk-" + "Zx" * 20)            # sk- lạ: nhiều ứng viên
    assert len(provs) >= 2 and needs is True
    assert top("random-text") == ([], True)          # không nhận ra -> không đoán
    assert top("") == ([], True)


def test_parse_json_array_variants():
    assert ai.parse_json_array('["a","b"]', 2) == ["a", "b"]
    assert ai.parse_json_array('```json\n["a","b"]\n```', 2) == ["a", "b"]
    assert ai.parse_json_array('Đây là kết quả: ["a", "b"] xong', 2) == ["a", "b"]
    assert ai.parse_json_array('["a"]', 2) is None            # sai số lượng
    assert ai.parse_json_array("không phải json", 1) is None
    assert ai.parse_json_array('[1, "x"]', 2) == ["1", "x"]


def test_make_batches_limits():
    lines = ["x" * 10] * 65
    b = ai.make_batches(lines, max_items=30, max_chars=10_000)
    assert [len(x) for x in b] == [30, 30, 5]
    big = ai.make_batches(["y" * 2000] * 4, max_items=30, max_chars=3500)
    assert all(len(x) <= 1 for x in big) and sum(len(x) for x in big) == 4


def test_translate_all_batches_dedups_and_saves_calls():
    calls = []

    def fake(ai_, system, user):
        arr = json.loads(user)
        calls.append(len(arr))
        return json.dumps([f"VI:{t}" for t in arr], ensure_ascii=False)

    orig = ai._complete_sync
    ai._complete_sync = fake
    try:
        tr = ai.LLMTranslator(ai.UserAI("openai", "m", "k"))
        texts = ["hello", "world", "hello", "bye"] * 20            # 80 dòng, chỉ 3 dòng khác nhau
        out = asyncio.run(tr.translate_all(texts, "vi", "en"))
    finally:
        ai._complete_sync = orig
    assert out == [f"VI:{t}" for t in texts]
    assert calls == [3]                                            # 80 dòng -> đúng 1 request cho 3 dòng duy nhất


def test_translate_all_falls_back_when_format_wrong():
    seq = {"n": 0}

    def fake(ai_, system, user):
        seq["n"] += 1
        if user.startswith("["):                                   # lô: trả sai định dạng
            return "xin lỗi tôi không làm được"
        return "T:" + user                                         # từng dòng: ok

    orig = ai._complete_sync
    ai._complete_sync = fake
    try:
        out = asyncio.run(ai.LLMTranslator(ai.UserAI("openai", "m", "k")).translate_all(["a", "b"], "vi"))
    finally:
        ai._complete_sync = orig
    assert out == ["T:a", "T:b"]


@contextlib.contextmanager
def mock_provider(routes):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            code, body = routes.get(self.path.split("?")[0], (404, {}))
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


def test_list_models_parsing_and_rejection():
    saved = {k: dict(v) for k, v in ai.PROVIDERS.items()}
    try:
        with mock_provider({
            "/models": (200, {"data": [{"id": "gpt-4o"}, {"id": "text-embedding-3"}, {"id": "whisper-1"}, {"id": "gpt-4o-mini"}]}),
        }) as base:
            ai.PROVIDERS["openai"]["base"] = base
            assert ai.list_models_sync("openai", "k") == ["gpt-4o", "gpt-4o-mini"]       # lọc embedding/whisper
        with mock_provider({"/models": (200, {"models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-3.1-flash-tts-preview", "supportedGenerationMethods": ["generateContent"]}]})}) as base:
            ai.PROVIDERS["gemini"]["base"] = base
            assert ai.list_models_sync("gemini", "k") == ["gemini-2.5-flash"]
        with mock_provider({"/models": (401, {"error": {"message": "bad key"}})}) as base:
            ai.PROVIDERS["openai"]["base"] = base
            assert raises(ai.AIKeyRejected, ai.list_models_sync, "openai", "k")
        with mock_provider({"/models": (500, {})}) as base:
            ai.PROVIDERS["openai"]["base"] = base
            assert raises(ai.AIError, ai.list_models_sync, "openai", "k")
    finally:
        ai.PROVIDERS.update(saved)
    assert raises(ai.AIError, ai.list_models_sync, "khong-co", "k")


def test_mask_key_never_reveals_middle():
    m = ai.mask_key("sk-abcdefghijklmnop1234")
    assert m == "sk-a…1234" and "efghij" not in m
