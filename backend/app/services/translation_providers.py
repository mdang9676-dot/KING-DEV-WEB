"""
TranslationProvider interface + các implementation cụ thể.

Thiết kế theo Strategy pattern để dễ thêm provider mới mà không sửa code gọi.
Nếu không có API key nào được cấu hình -> dùng DemoTranslationProvider
(rõ ràng ghi là DEMO, không giả vờ là kết quả AI thật).
"""
from abc import ABC, abstractmethod

import httpx

from app.core.config import settings


class TranslationProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        ...

    async def translate_batch(self, texts: list[str], target_lang: str, source_lang: str | None = None) -> list[str]:
        # Implementation mặc định: gọi tuần tự. Provider con có thể override để batch thật.
        return [await self.translate(t, target_lang, source_lang) for t in texts]


class OpenAICompatibleProvider(TranslationProvider):
    """Dùng cho OpenAI thật hoặc bất kỳ endpoint OpenAI-compatible nào (đổi OPENAI_BASE_URL)."""
    name = "openai_compatible"

    def __init__(self, api_key: str, base_url: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        prompt = (
            f"Translate the following text to {target_lang}. "
            f"Only return the translated text, no explanation:\n\n{text}"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()


class GeminiProvider(TranslationProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model

    async def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        prompt = f"Translate to {target_lang}, return only the translation:\n\n{text}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()


class DeepSeekProvider(TranslationProvider):
    name = "deepseek"

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        prompt = f"Translate to {target_lang}, return only the translation:\n\n{text}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()


class ArgosLocalProvider(TranslationProvider):
    """Dịch local, offline, miễn phí bằng Argos Translate (không cần API key,
    nhưng cần tải gói ngôn ngữ trước - xem docs/INSTALL.md)."""
    name = "argos_local"

    async def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        try:
            import argostranslate.translate as at
        except ImportError as e:
            raise RuntimeError(
                "argostranslate chưa được cài hoặc chưa tải gói ngôn ngữ. "
                "Xem docs/INSTALL.md phần Local Translation."
            ) from e
        import asyncio
        return await asyncio.to_thread(at.translate, text, source_lang or "en", target_lang)


class DemoTranslationProvider(TranslationProvider):
    """Provider giả lập dùng khi KHÔNG có API key và KHÔNG có local model.
    Trả về text gốc kèm nhãn [DEMO] để UI không hiểu nhầm là bản dịch thật."""
    name = "demo"

    async def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        return f"[DEMO:{target_lang}] {text}"


def get_translation_provider(preferred: str | None = None) -> TranslationProvider:
    """
    Factory: chọn provider dựa trên preferred (nếu có) hoặc theo API key nào
    được cấu hình trong .env. Luôn fallback về Demo nếu không có gì khả dụng.
    """
    if preferred == "openai" and settings.OPENAI_API_KEY:
        return OpenAICompatibleProvider(settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL)
    if preferred == "gemini" and settings.GEMINI_API_KEY:
        return GeminiProvider(settings.GEMINI_API_KEY)
    if preferred == "deepseek" and settings.DEEPSEEK_API_KEY:
        return DeepSeekProvider(settings.DEEPSEEK_API_KEY)
    if preferred == "argos_local":
        return ArgosLocalProvider()

    # Auto-detect nếu không chỉ định preferred
    if settings.OPENAI_API_KEY:
        return OpenAICompatibleProvider(settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL)
    if settings.GEMINI_API_KEY:
        return GeminiProvider(settings.GEMINI_API_KEY)
    if settings.DEEPSEEK_API_KEY:
        return DeepSeekProvider(settings.DEEPSEEK_API_KEY)

    return DemoTranslationProvider()
