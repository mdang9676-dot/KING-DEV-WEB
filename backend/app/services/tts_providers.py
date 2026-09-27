"""
VoiceProvider interface cho AI dubbing.

QUAN TRỌNG - Voice cloning:
Nếu provider hỗ trợ voice cloning (vd XTTS), hệ thống CHỈ được dùng khi:
- user tự upload audio reference của CHÍNH GIỌNG HỌ (hoặc giọng họ có quyền dùng), và
- không có danh sách giọng người nổi tiếng dựng sẵn để user chọn "giả giọng" ai đó.
Việc enforce quyền sử dụng giọng nằm ở services/voice_clones.py (bắt buộc consent khi tạo mẫu giọng, mẫu chỉ
thuộc về đúng người tải lên) - provider chỉ cung cấp khả năng kỹ thuật.
"""
import asyncio
import re
import threading
import unicodedata
import wave
from abc import ABC, abstractmethod
from pathlib import Path


class VoiceProvider(ABC):
    name: str = "base"
    supports_cloning: bool = False

    @abstractmethod
    async def list_voices(self, language: str | None = None) -> list[dict]:
        ...

    @abstractmethod
    async def synthesize(self, text: str, voice_id: str, out_path: Path, rate: float = 1.0) -> Path:
        ...


class EdgeTTSProvider(VoiceProvider):
    """Microsoft Edge TTS - miễn phí, cần internet, không cần API key."""
    name = "edge_tts"

    async def list_voices(self, language: str | None = None) -> list[dict]:
        import edge_tts
        voices = await edge_tts.list_voices()
        if language:
            voices = [v for v in voices if v["Locale"].lower().startswith(language.lower())]
        return [{"id": v["ShortName"], "name": v["FriendlyName"], "locale": v["Locale"], "gender": v.get("Gender", "")} for v in voices]

    async def synthesize(self, text: str, voice_id: str, out_path: Path, rate: float = 1.0) -> Path:
        import edge_tts
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rate_str = f"{'+' if rate >= 1 else ''}{int((rate - 1) * 100)}%"
        communicate = edge_tts.Communicate(text, voice_id, rate=rate_str)
        await communicate.save(str(out_path))
        return out_path


class PiperProvider(VoiceProvider):
    """Piper TTS - chạy local hoàn toàn offline, cần model .onnx tải trước."""
    name = "piper"

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir

    async def list_voices(self, language: str | None = None) -> list[dict]:
        if not self.model_dir.exists():
            return []
        voices = []
        for f in self.model_dir.glob("*.onnx"):
            voices.append({"id": f.stem, "name": f.stem, "locale": "local"})
        return voices

    async def synthesize(self, text: str, voice_id: str, out_path: Path, rate: float = 1.0) -> Path:
        import asyncio
        model_path = self.model_dir / f"{voice_id}.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"Piper model không tồn tại: {model_path}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "piper", "--model", str(model_path), "--output_file", str(out_path),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=text.encode("utf-8"))
        return out_path


class OpenAITTSProvider(VoiceProvider):
    name = "openai_tts"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def list_voices(self, language: str | None = None) -> list[dict]:
        # OpenAI TTS có danh sách giọng cố định
        fixed = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        return [{"id": v, "name": v, "locale": "multi"} for v in fixed]

    async def synthesize(self, text: str, voice_id: str, out_path: Path, rate: float = 1.0) -> Path:
        import httpx
        out_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": "tts-1", "voice": voice_id, "input": text, "speed": rate},
            )
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
        return out_path


class GeminiTTSProvider(VoiceProvider):
    """Giọng Gemini (API key Gemini của người dùng). Trả về PCM 24kHz mono 16-bit -> bọc thành WAV."""
    name = "gemini_tts"
    VOICES = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
              "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
              "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelmahi",
              "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"]

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    async def list_voices(self, language: str | None = None) -> list[dict]:
        # Gemini tự nhận ngôn ngữ từ văn bản nên một giọng đọc được nhiều ngôn ngữ
        return [{"id": v, "name": v, "locale": "multi", "gender": ""} for v in self.VOICES]

    def _call(self, text: str, voice_id: str) -> bytes:
        import base64
        import json
        import urllib.error
        import urllib.request
        if voice_id not in self.VOICES:
            raise ValueError(f"Giọng Gemini không hợp lệ: {voice_id}")
        body = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}}}},
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RuntimeError("Gemini TTS vượt hạn mức (429). Hãy đợi một lúc hoặc dùng giọng Edge.")
            if e.code in (401, 403):
                raise RuntimeError("Gemini từ chối API key.")
            raise RuntimeError(f"Gemini TTS lỗi HTTP {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Không kết nối được Gemini: {e}")
        try:
            return base64.b64decode(data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("Gemini không trả về âm thanh (nội dung có thể bị từ chối).")

    async def synthesize(self, text: str, voice_id: str, out_path: Path, rate: float = 1.0) -> Path:
        import asyncio
        import wave
        pcm = await asyncio.to_thread(self._call, text, voice_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        return out_path


# ---------------------------------------------------------------------------
# VieNeu-TTS — tự host, Apache-2.0, tiếng Việt, NHÂN BẢN GIỌNG từ mẫu 3–8 giây
# (https://github.com/pnnbao97/VieNeu-TTS ; cài bằng: pip install -r requirements-voice.txt)
# ---------------------------------------------------------------------------
PRESET_PREFIX = "preset:"
CLONE_PREFIX = "clone:"
_ENGINE_LOCK = threading.Lock()      # bảo vệ việc nạp model (chỉ nạp 1 lần / tiến trình)
_INFER_LOCK = threading.Lock()       # engine ONNX dùng chung -> tổng hợp tuần tự, không giả định thread-safe
_ENGINES: dict[tuple, object] = {}
_REGISTERED: set[tuple[int, str]] = set()   # (id(engine), tên giọng đã add_voice) để không mã hoá lại mẫu mỗi câu


def vieneu_installed() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("vieneu") is not None
    except (ImportError, ValueError):
        return False


def get_vieneu_engine(mode: str = "v3turbo", precision: str = "fp32", backend: str = ""):
    """Nạp model VieNeu đúng 1 lần cho mỗi cấu hình (nạp mất ~15-20 giây lần đầu + tải model từ Hugging Face)."""
    key = (mode, precision, backend)
    with _ENGINE_LOCK:
        eng = _ENGINES.get(key)
        if eng is None:
            try:
                from vieneu import Vieneu
            except ImportError as e:
                raise RuntimeError("Chưa cài VieNeu-TTS. Chạy: pip install -r requirements-voice.txt") from e
            kwargs: dict = {}
            if mode and mode != "v3turbo":
                kwargs["mode"] = mode
            if precision and precision != "fp32":
                kwargs["precision"] = precision
            if backend:
                kwargs["backend"] = backend
            eng = Vieneu(**kwargs)
            _ENGINES[key] = eng
        return eng


def _slug(label: str) -> str:
    s = unicodedata.normalize("NFKD", (label or "").replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()[:50] or "voice"


def _write_wav(audio, sample_rate: int, path: Path) -> None:
    """Ghi mảng âm thanh (float -1..1) ra WAV 16-bit mono. Dùng numpy nếu có (luôn có khi cài VieNeu)."""
    try:
        import numpy as np
        a = np.asarray(audio).reshape(-1)
        if a.size == 0:
            raise RuntimeError("VieNeu không trả về âm thanh.")
        if np.issubdtype(a.dtype, np.floating):
            pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        else:
            pcm = a.astype("<i2").tobytes()
    except ImportError:
        from array import array
        import sys
        vals = [max(-32768, min(32767, int(x * 32767))) for x in audio]
        if not vals:
            raise RuntimeError("VieNeu không trả về âm thanh.")
        arr = array("h", vals)
        if sys.byteorder == "big":
            arr.byteswap()
        pcm = arr.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)


def forget_vieneu_clone(owner: str, clone_id: str) -> None:
    """Gỡ giọng nhân bản đã xoá khỏi bộ nhớ engine (đặc trưng người nói không được giữ lại sau khi người dùng xoá)."""
    name = f"u{owner[:8]}_{clone_id}"
    with _INFER_LOCK:
        for eng in list(_ENGINES.values()):
            if (id(eng), name) in _REGISTERED:
                _REGISTERED.discard((id(eng), name))
                rm = getattr(eng, "remove_voice", None)
                if callable(rm):
                    try:
                        rm(name)
                    except Exception:                       # noqa: BLE001  (không để lỗi engine chặn việc xoá)
                        pass


class VieNeuProvider(VoiceProvider):
    """
    Giọng VieNeu-TTS chạy ngay trên máy chủ của bạn (không gửi văn bản/giọng ra ngoài).

    voice_id có 2 dạng:
      • "preset:<slug>"  giọng dựng sẵn của model (không cần mẫu)
      • "clone:<hex32>"  giọng NHÂN BẢN từ mẫu của chính người dùng (xem services/voice_clones.py)
    """
    name = "vieneu"
    supports_cloning = True

    def __init__(self, clones_dir: Path | None = None, owner: str = "", mode: str = "v3turbo",
                 precision: str = "fp32", backend: str = "", engine=None):
        self.clones_dir, self.owner = clones_dir, owner
        self.mode, self.precision, self.backend = mode, precision, backend
        self._eng = engine

    # -- engine ----------------------------------------------------------
    def _engine(self):
        if self._eng is None:
            self._eng = get_vieneu_engine(self.mode, self.precision, self.backend)
        return self._eng

    def _sample_rate(self, eng) -> int:
        sr = getattr(eng, "sample_rate", None)
        if isinstance(sr, int) and sr > 0:
            return sr
        return 24000 if self.mode == "v3nano" else 48000     # Turbo 48 kHz, Nano 24 kHz (theo tài liệu VieNeu)

    def _presets(self, eng) -> dict[str, tuple[str, str]]:
        """slug -> (nhãn hiển thị, giá trị truyền vào infer(voice=...))."""
        out: dict[str, tuple[str, str]] = {}
        for item in eng.list_preset_voices():
            label, vid = (item, item) if isinstance(item, str) else (item[0], item[1] if len(item) > 1 else item[0])
            out.setdefault(_slug(str(label)), (str(label), str(vid)))
        return out

    # -- VoiceProvider ---------------------------------------------------
    async def list_voices(self, language: str | None = None) -> list[dict]:
        def load():
            with _INFER_LOCK:
                return self._presets(self._engine())
        presets = await asyncio.to_thread(load)
        return [{"id": PRESET_PREFIX + slug, "name": label, "locale": "vi", "gender": "", "cloned": False}
                for slug, (label, _) in presets.items()]

    def _synth_blocking(self, text: str, voice_id: str, out_path: Path) -> None:
        eng = self._engine()
        with _INFER_LOCK:
            if voice_id.startswith(CLONE_PREFIX):
                from app.services import voice_clones
                if self.clones_dir is None:
                    raise ValueError("Thiếu thư mục giọng nhân bản.")
                cid = voice_id[len(CLONE_PREFIX):]
                ref = voice_clones.clone_path(self.clones_dir, cid)     # kiểm tra định dạng + tồn tại + đúng chủ
                name = f"u{self.owner[:8]}_{cid}"
                if hasattr(eng, "add_voice"):
                    if (id(eng), name) not in _REGISTERED:
                        eng.add_voice(name, str(ref))                   # mã hoá mẫu giọng 1 lần, dùng lại cho mọi câu
                        _REGISTERED.add((id(eng), name))
                    audio = eng.infer(text, voice=name)
                else:
                    audio = eng.infer(text, ref_audio=str(ref))
            elif voice_id.startswith(PRESET_PREFIX):
                slug = voice_id[len(PRESET_PREFIX):]
                found = self._presets(eng).get(slug)
                if not found:
                    raise ValueError(f"Giọng VieNeu không tồn tại: {slug}")
                label, vid = found
                try:
                    audio = eng.infer(text, voice=vid)
                except (KeyError, ValueError):
                    audio = eng.infer(text, voice=label)
            else:
                raise ValueError("voice_id VieNeu phải bắt đầu bằng 'preset:' hoặc 'clone:'.")
        _write_wav(audio, self._sample_rate(eng), out_path)

    async def synthesize(self, text: str, voice_id: str, out_path: Path, rate: float = 1.0) -> Path:
        # `rate` bị bỏ qua có chủ đích: dubbing_service tự tăng tốc bằng atempo để khớp thời lượng câu.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._synth_blocking, text, voice_id, out_path)
        return out_path


class DemoVoiceProvider(VoiceProvider):
    """Provider giả lập: tạo file audio im lặng đúng độ dài ước tính, dùng khi
    không có TTS engine nào khả dụng. UI phải hiển thị rõ đây là DEMO MODE."""
    name = "demo"

    async def list_voices(self, language: str | None = None) -> list[dict]:
        return [{"id": "demo-voice", "name": "Demo Voice (silent)", "locale": "demo"}]

    async def synthesize(self, text: str, voice_id: str, out_path: Path, rate: float = 1.0) -> Path:
        import asyncio
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Ước lượng ~ 3 từ/giây để tạo silence có độ dài tương ứng
        duration = max(1.0, len(text.split()) / 3.0)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(duration), str(out_path),
        )
        await proc.communicate()
        return out_path


def get_voice_provider(name: str, **kwargs) -> VoiceProvider:
    providers = {
        "edge_tts": lambda: EdgeTTSProvider(),
        "piper": lambda: PiperProvider(kwargs.get("model_dir", Path("data/piper_models"))),
        "openai_tts": lambda: OpenAITTSProvider(kwargs["api_key"]) if kwargs.get("api_key") else DemoVoiceProvider(),
        "gemini_tts": lambda: GeminiTTSProvider(kwargs["api_key"], kwargs.get("model", "gemini-3.1-flash-tts-preview")) if kwargs.get("api_key") else DemoVoiceProvider(),
        "vieneu": lambda: VieNeuProvider(kwargs.get("clones_dir"), kwargs.get("owner", ""), kwargs.get("mode", "v3turbo"),
                                         kwargs.get("precision", "fp32"), kwargs.get("backend", "")),
        "demo": lambda: DemoVoiceProvider(),
    }
    factory = providers.get(name, providers["demo"])
    return factory()
