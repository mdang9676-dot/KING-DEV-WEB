"""
Kết nối AI bằng API key CỦA NGƯỜI DÙNG (Gemini, OpenAI, Claude, DeepSeek, Groq, OpenRouter, xAI, Mistral, ...).

- detect_provider: đoán nhà cung cấp từ dạng key. Chỉ "chắc chắn" khi tiền tố đặc trưng; còn lại trả nhiều
  ứng viên để NGƯỜI DÙNG chọn (không đoán mò, và không bao giờ thử key lên nhiều nhà cung cấp — tránh lộ key).
- list_models: gọi đúng nhà cung cấp đã chọn để xác minh key và liệt kê model thật.
- LLMTranslator: dịch phụ đề THEO LÔ (tối đa ~30 dòng/lần, bỏ dòng trùng) -> ít request, ít token, ngữ cảnh tốt hơn.
- Base URL cố định trong bảng bên dưới (không cho người dùng tự nhập URL) để tránh SSRF.
"""
import asyncio
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Awaitable, Callable

PROVIDERS: dict[str, dict] = {
    "gemini": {"label": "Google Gemini", "kind": "gemini", "base": "https://generativelanguage.googleapis.com/v1beta"},
    "openai": {"label": "OpenAI", "kind": "openai", "base": "https://api.openai.com/v1"},
    "anthropic": {"label": "Anthropic Claude", "kind": "anthropic", "base": "https://api.anthropic.com/v1"},
    "deepseek": {"label": "DeepSeek", "kind": "openai", "base": "https://api.deepseek.com"},
    "groq": {"label": "Groq", "kind": "openai", "base": "https://api.groq.com/openai/v1"},
    "openrouter": {"label": "OpenRouter", "kind": "openai", "base": "https://openrouter.ai/api/v1"},
    "xai": {"label": "xAI Grok", "kind": "openai", "base": "https://api.x.ai/v1"},
    "mistral": {"label": "Mistral", "kind": "openai", "base": "https://api.mistral.ai/v1"},
    "together": {"label": "Together AI", "kind": "openai", "base": "https://api.together.xyz/v1"},
    "fireworks": {"label": "Fireworks AI", "kind": "openai", "base": "https://api.fireworks.ai/inference/v1"},
    "nvidia": {"label": "NVIDIA NIM", "kind": "openai", "base": "https://integrate.api.nvidia.com/v1"},
    "cerebras": {"label": "Cerebras", "kind": "openai", "base": "https://api.cerebras.ai/v1"},
}

LANG_NAMES = {
    "vi": "Vietnamese", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French",
    "de": "German", "es": "Spanish", "pt": "Portuguese", "ru": "Russian", "th": "Thai", "id": "Indonesian",
    "ar": "Arabic", "hi": "Hindi",
}

_NON_CHAT = ("embed", "whisper", "tts", "dall-e", "moderation", "image", "imagen", "audio", "veo", "aqa",
             "transcribe", "realtime", "rerank", "guard", "video")


class AIError(Exception):
    pass


class AIKeyRejected(AIError):
    """Nhà cung cấp từ chối key (401/403)."""


def provider_list() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in PROVIDERS.items()]


def mask_key(key: str) -> str:
    return (key[:4] + "…" + key[-4:]) if len(key) > 10 else "••••"


# ---------------------------------------------------------------------------
# Nhận diện nhà cung cấp
# ---------------------------------------------------------------------------

def detect_provider(key: str) -> dict:
    k = (key or "").strip()

    def one(pid: str, conf: str) -> dict:
        return {"provider": pid, "label": PROVIDERS[pid]["label"], "confidence": conf}

    cands: list[dict] = []
    if k.startswith("sk-ant-"):
        cands = [one("anthropic", "high")]
    elif k.startswith("sk-or-"):
        cands = [one("openrouter", "high")]
    elif k.startswith(("sk-proj-", "sk-svcacct-", "sk-admin-")):
        cands = [one("openai", "high")]
    elif k.startswith("AIza") and len(k) == 39:
        cands = [one("gemini", "high")]
    elif k.startswith("gsk_"):
        cands = [one("groq", "high")]
    elif k.startswith("xai-"):
        cands = [one("xai", "high")]
    elif k.startswith("nvapi-"):
        cands = [one("nvidia", "high")]
    elif k.startswith("csk-"):
        cands = [one("cerebras", "high")]
    elif k.startswith("fw_"):
        cands = [one("fireworks", "high")]
    elif re.fullmatch(r"sk-[0-9a-f]{32}", k):
        cands = [one("deepseek", "medium")]
    elif k.startswith("sk-") and "T3BlbkFJ" in k:
        cands = [one("openai", "high")]
    elif k.startswith("sk-"):
        cands = [one("openai", "medium"), one("deepseek", "low")]
    elif re.fullmatch(r"[0-9a-f]{64}", k):
        cands = [one("together", "medium")]
    elif re.fullmatch(r"[A-Za-z0-9]{32}", k):
        cands = [one("mistral", "medium")]
    needs_choice = not (len(cands) == 1 and cands[0]["confidence"] == "high")
    return {"candidates": cands, "needs_choice": needs_choice}


# ---------------------------------------------------------------------------
# HTTP tối giản (urllib chạy trong thread)
# ---------------------------------------------------------------------------

def _http(method: str, url: str, headers: dict, body: dict | None, timeout: float) -> tuple[int, dict | None]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read(65536)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AIError(f"Không kết nối được nhà cung cấp AI: {e}") from e
    try:
        return status, json.loads(raw)
    except (ValueError, TypeError):
        return status, None


def _auth_headers(kind: str, key: str) -> dict:
    if kind == "gemini":
        return {"x-goog-api-key": key}
    if kind == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def _err_message(data: dict | None) -> str:
    if isinstance(data, dict):
        e = data.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e)[:200]
        if e:
            return str(e)[:200]
    return ""


def list_models_sync(provider: str, key: str) -> list[str]:
    if provider not in PROVIDERS:
        raise AIError("Nhà cung cấp không được hỗ trợ.")
    cfg = PROVIDERS[provider]
    kind = cfg["kind"]
    url = cfg["base"] + ("/models?pageSize=200" if kind == "gemini" else "/models?limit=100" if kind == "anthropic" else "/models")
    status, data = _http("GET", url, _auth_headers(kind, key), None, 15)
    if status in (401, 403):
        raise AIKeyRejected(f"{cfg['label']} từ chối API key này (sai key hoặc không có quyền).")
    if status != 200 or not isinstance(data, dict):
        raise AIError(f"{cfg['label']} phản hồi lỗi (HTTP {status}). {_err_message(data)}")

    ids: list[str] = []
    if kind == "gemini":
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                ids.append(str(m.get("name", "")).removeprefix("models/"))
    else:
        ids = [str(m.get("id", "")) for m in data.get("data", []) if isinstance(m, dict)]
    ids = [i for i in ids if i and not any(w in i.lower() for w in _NON_CHAT)]
    return sorted(set(ids))


async def list_models(provider: str, key: str) -> list[str]:
    return await asyncio.to_thread(list_models_sync, provider, key)


# ---------------------------------------------------------------------------
# Gọi LLM
# ---------------------------------------------------------------------------

@dataclass
class UserAI:
    provider: str
    model: str
    api_key: str


def _complete_sync(ai: UserAI, system: str, user: str) -> str:
    cfg = PROVIDERS[ai.provider]
    kind, base = cfg["kind"], cfg["base"]
    headers = _auth_headers(kind, ai.api_key)
    if kind == "gemini":
        url = f"{base}/models/{ai.model}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.2}}
    elif kind == "anthropic":
        url = f"{base}/messages"
        body = {"model": ai.model, "max_tokens": 4096, "system": system,
                "messages": [{"role": "user", "content": user}]}
    else:
        url = f"{base}/chat/completions"
        body = {"model": ai.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}

    status, data = _http("POST", url, headers, body, 90)
    if status in (401, 403):
        raise AIKeyRejected(f"{cfg['label']} từ chối API key.")
    if status == 429:
        raise AIError("Vượt giới hạn tốc độ/hạn mức của nhà cung cấp AI (429).")
    if status != 200 or not isinstance(data, dict):
        raise AIError(f"{cfg['label']} lỗi HTTP {status}. {_err_message(data)}")
    try:
        if kind == "gemini":
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
        if kind == "anthropic":
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise AIError("Phản hồi của nhà cung cấp AI không đúng định dạng.")


def parse_json_array(text: str, n: int) -> list[str] | None:
    """Trích mảng JSON gồm đúng n chuỗi từ phản hồi của LLM (chịu được ```json ... ``` và lời dẫn thừa)."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    i, j = t.find("["), t.rfind("]")
    if i == -1 or j <= i:
        return None
    try:
        arr = json.loads(t[i:j + 1])
    except ValueError:
        return None
    if not isinstance(arr, list) or len(arr) != n:
        return None
    return [x if isinstance(x, str) else str(x) for x in arr]


def make_batches(lines: list[str], max_items: int = 30, max_chars: int = 3500) -> list[list[int]]:
    """Chia chỉ số dòng thành các lô theo số dòng và số ký tự."""
    batches, cur, size = [], [], 0
    for idx, line in enumerate(lines):
        if cur and (len(cur) >= max_items or size + len(line) > max_chars):
            batches.append(cur)
            cur, size = [], 0
        cur.append(idx)
        size += len(line)
    if cur:
        batches.append(cur)
    return batches


STYLE_GUIDES = {
    "modern": {"vi": "Setting: modern day. Use modern Vietnamese pronouns and address terms (tôi - anh/chị/em, anh - em, "
                     "bạn - tôi); avoid archaic forms.",
               "*": "Setting: modern day. Use natural, contemporary spoken language."},
    "ancient": {"vi": "Setting: ancient / wuxia / xianxia. Use period Vietnamese pronouns and address terms (ta - ngươi/ngài, "
                      "huynh/đệ/muội/tỷ, sư phụ/sư huynh/sư đệ, tại hạ, cô nương...) and a formal, slightly archaic register; "
                      "avoid modern slang.",
                "*": "Setting: ancient / historical. Use a formal, slightly archaic register suited to period dialogue."},
    "anime": {"vi": "Anime style: casual youthful Vietnamese (tớ - cậu, mình - bạn, anh/chị - em); keep honorific nuances "
                    "(senpai, -san...) the way Vietnamese fan localizations usually do.",
              "*": "Anime style: casual, youthful dialogue; keep honorific nuances naturally."},
}


def style_prompt(style: str | None, target_lang: str) -> str:
    """Đoạn hướng dẫn xưng hô/giọng văn thêm vào prompt dịch (rỗng nếu 'auto')."""
    g = STYLE_GUIDES.get(style or "")
    if not g:
        return ""
    return " " + (g.get(target_lang) or g["*"])


class LLMTranslator:
    """Dịch phụ đề bằng LLM của người dùng. Có translate_all (theo lô) để pipeline dùng."""
    name = "user_ai"

    def __init__(self, ai: UserAI):
        self.ai = ai

    async def _complete(self, system: str, user: str) -> str:
        for attempt in range(3):
            try:
                return await asyncio.to_thread(_complete_sync, self.ai, system, user)
            except AIKeyRejected:
                raise
            except AIError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
        raise AIError("unreachable")

    async def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        tgt = LANG_NAMES.get(target_lang, target_lang)
        out = await self._complete(
            f"Translate the user's subtitle line into {tgt}. Reply with the translation only.", text)
        return out.strip()

    async def translate_all(
        self, texts: list[str], target_lang: str, source_lang: str | None = None,
        on_progress: Callable[[float], Awaitable[None]] | None = None,
        glossary: list[tuple[str, str]] | None = None,
        style: str | None = None,
    ) -> list[str]:
        from app.services.glossary import glossary_prompt
        tgt = LANG_NAMES.get(target_lang, target_lang)
        src = f" from {LANG_NAMES.get(source_lang, source_lang)}" if source_lang else ""
        system = (f"You are a professional subtitle translator. Translate each item{src} into {tgt}. Keep the meaning and "
                  f"tone; keep lines short and natural for subtitles. Do not merge, split, skip or add items. "
                  f"Reply with ONLY a JSON array of strings with exactly the same number of items, in the same order."
                  + style_prompt(style, target_lang) + glossary_prompt(glossary or []))

        unique = list(dict.fromkeys(t.strip() for t in texts))     # bỏ dòng trùng -> tiết kiệm token
        result: dict[str, str] = {}
        batches = make_batches(unique)
        sem, done = asyncio.Semaphore(3), 0

        async def run(idxs: list[int]):
            nonlocal done
            batch = [unique[i] for i in idxs]
            async with sem:
                out = parse_json_array(await self._complete(system, json.dumps(batch, ensure_ascii=False)), len(batch))
                if out is None:                                     # định dạng sai: thử lại 1 lần, rồi dịch từng dòng
                    out = parse_json_array(await self._complete(system + " Output must be valid JSON.", json.dumps(batch, ensure_ascii=False)), len(batch))
                if out is None:
                    out = [await self.translate(b, target_lang, source_lang) for b in batch]
            for src_line, tr in zip(batch, out):
                result[src_line] = tr.strip()
            done += 1
            if on_progress:
                await on_progress(done / max(1, len(batches)))

        from app.services.errors import gather_or_cancel
        await gather_or_cancel(*(run(b) for b in batches))
        return [result[t.strip()] for t in texts]

    async def shorten(self, items: list[tuple[str, int]], target_lang: str) -> list[str | None]:
        """Rút gọn các dòng quá dài: items=[(bản dịch, số ký tự tối đa)] -> list bản rút gọn (None = giữ nguyên)."""
        from app.services.subtitle_qc import pick_shorter
        tgt = LANG_NAMES.get(target_lang, target_lang)
        system = (f"You edit subtitles written in {tgt}. For each item, rewrite 'text' to at most 'max_chars' characters "
                  f"(including spaces), keeping the key meaning and natural {tgt}. If it already fits, return it unchanged. "
                  f"Reply with ONLY a JSON array of strings, same order and same number of items as the input.")
        results: list[str | None] = [None] * len(items)
        for idxs in make_batches([t for t, _ in items], 30, 3500):
            payload = json.dumps([{"text": items[i][0], "max_chars": items[i][1]} for i in idxs], ensure_ascii=False)
            out = parse_json_array(await self._complete(system, payload), len(idxs))
            if out:
                for i, o in zip(idxs, out):
                    results[i] = pick_shorter(items[i][0], o)
        return results


async def load_user_ai(user_id: str) -> UserAI | None:
    """Đọc + giải mã cấu hình AI đã lưu của người dùng (None nếu chưa lưu)."""
    from sqlalchemy import select
    from app.core import crypto
    from app.models.db import UserAISettings, async_session
    async with async_session() as s:
        row = (await s.execute(select(UserAISettings).where(UserAISettings.user_id == user_id))).scalar_one_or_none()
    if not row:
        return None
    try:
        return UserAI(row.provider, row.model or "", crypto.decrypt_text(row.key_enc))
    except crypto.SecretDecryptError:
        return None
