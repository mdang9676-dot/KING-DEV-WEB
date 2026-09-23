import asyncio
import tempfile
import wave
from pathlib import Path

from app.services.tts_providers import GeminiTTSProvider


def test_gemini_wraps_pcm_into_valid_wav_and_rejects_bad_voice():
    p = GeminiTTSProvider("key", "model")
    p._call = lambda text, voice: b"\x01\x00" * 2400                    # 100ms PCM giả (24kHz mono s16)
    out = Path(tempfile.mkdtemp()) / "x.audio"
    asyncio.run(p.synthesize("KINGWEB DEV", "Kore", out))
    with wave.open(str(out)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 2400)
    assert len(asyncio.run(p.list_voices())) == 30
    q = GeminiTTSProvider("key", "model")
    try:
        q._call("hi", "../../etc")
        assert False
    except ValueError:
        pass
