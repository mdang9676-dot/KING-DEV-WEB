"""
Lưu & quản lý MẪU GIỌNG nhân bản của người dùng (không phụ thuộc engine TTS nào).

Bố cục:  <VOICES_DIR>/<user_id>/<clone_id>.wav   (mono, 44.1 kHz, ≤ 12 giây, đã cắt khoảng lặng đầu)
                                 <clone_id>.json  (tên, thời điểm, XÁC NHẬN QUYỀN SỬ DỤNG GIỌNG)

Nguyên tắc an toàn:
- Bắt buộc `consent=True` (người dùng xác nhận đây là giọng của chính họ hoặc họ có quyền dùng) — kiểm tra ở
  tầng service để dù UI/route nào gọi cũng không bỏ qua được. Thời điểm xác nhận được lưu kèm mẫu giọng.
- Không có giọng người nổi tiếng dựng sẵn; mẫu chỉ đọc/nghe/xoá được bởi CHÍNH chủ (đường dẫn luôn nằm trong
  thư mục của user_id, id phải là hex32 -> không path traversal).
- Nội dung file do ffprobe/ffmpeg kiểm tra thật (không tin đuôi file hay Content-Type client gửi).
"""
import asyncio
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

HEX32 = re.compile(r"^[0-9a-f]{32}$")
MIN_SEC = 2.5            # ngắn hơn: không đủ đặc trưng giọng
KEEP_SEC = 12.0          # engine chỉ dùng ≤ ~8 giây, giữ dư một chút phòng khi cắt khoảng lặng
SAMPLE_RATE = 44100
NAME_MAX = 40


class CloneError(ValueError):
    """Lỗi người dùng có thể sửa được (thông điệp tiếng Việt, hiển thị thẳng lên UI)."""


def user_dir(base: Path, user_id: str) -> Path:
    if not HEX32.match(user_id or ""):
        raise CloneError("Tài khoản không hợp lệ.")
    return base / user_id


def clone_path(udir: Path, clone_id: str) -> Path:
    """Đường dẫn file wav của mẫu giọng; ném CloneError nếu id sai định dạng hoặc mẫu không tồn tại."""
    if not HEX32.match(clone_id or ""):
        raise CloneError("Mã giọng nhân bản không hợp lệ.")
    p = udir / f"{clone_id}.wav"
    if not p.is_file():
        raise CloneError("Giọng nhân bản không tồn tại (có thể đã bị xoá). Hãy chọn giọng khác.")
    return p


def sanitize_name(name: str) -> str:
    n = "".join(c for c in unicodedata.normalize("NFC", name or "") if unicodedata.category(c)[0] != "C")
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        raise CloneError("Hãy đặt tên cho giọng nhân bản.")
    return n[:NAME_MAX]


def list_clones(udir: Path) -> list[dict]:
    out: list[dict] = []
    if not udir.is_dir():
        return out
    for meta in udir.glob("*.json"):
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
            if HEX32.match(d.get("id", "")) and (udir / f"{d['id']}.wav").is_file():
                out.append({"id": d["id"], "name": d.get("name", "Giọng của tôi"),
                            "created_at": d.get("created_at"), "duration_sec": d.get("duration_sec")})
        except (OSError, ValueError):
            continue
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def delete_clone(udir: Path, clone_id: str) -> bool:
    if not HEX32.match(clone_id or ""):
        return False
    existed = False
    for suffix in (".wav", ".json", ".preview.wav"):        # gồm cả bản nghe thử tổng hợp từ giọng này
        p = udir / f"{clone_id}{suffix}"
        if p.exists():
            p.unlink(missing_ok=True)
            existed = True
    return existed


async def _run(*args: str, timeout: float = 90) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise CloneError("Xử lý file âm thanh quá lâu. Hãy dùng file ngắn hơn (3–10 giây).")
    return proc.returncode or 0, out, err


async def _probe(src: Path) -> tuple[bool, float]:
    """(có luồng âm thanh?, thời lượng giây) đọc từ nội dung thật của file."""
    code, out, _ = await _run("ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                              "stream=codec_type:format=duration", "-of", "json", str(src), timeout=30)
    if code != 0:
        return False, 0.0
    try:
        d = json.loads(out or b"{}")
    except ValueError:
        return False, 0.0
    has_audio = bool(d.get("streams"))
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return has_audio, dur


async def create_clone(udir: Path, src: Path, name: str, consent: bool, max_clones: int = 10, audit: dict | None = None) -> dict:
    """Chuẩn hoá file người dùng tải lên thành mẫu giọng và lưu lại. Ném CloneError nếu không hợp lệ."""
    if consent is not True:
        raise CloneError("Bạn cần xác nhận đây là giọng của chính bạn hoặc bạn có quyền sử dụng giọng này.")
    name = sanitize_name(name)
    udir.mkdir(parents=True, exist_ok=True)
    if len(list_clones(udir)) >= max_clones:
        raise CloneError(f"Đã đạt giới hạn {max_clones} giọng nhân bản. Hãy xoá bớt giọng cũ.")

    has_audio, dur = await _probe(src)
    if not has_audio:
        raise CloneError("File không có âm thanh đọc được. Hãy tải file ghi âm giọng nói (WAV/MP3/M4A…).")
    if 0 < dur < MIN_SEC:
        raise CloneError(f"Mẫu giọng quá ngắn ({dur:.1f}s). Cần tối thiểu {MIN_SEC:g} giây, tốt nhất 5–10 giây rõ tiếng.")

    clone_id = uuid.uuid4().hex
    wav = udir / f"{clone_id}.wav"
    tmp = udir / f".{clone_id}.part.wav"
    code, _, err = await _run(
        "ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(src), "-vn",
        "-af", "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-45dB",
        "-t", str(KEEP_SEC), "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp))
    if code != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise CloneError("Không đọc được file âm thanh này (file hỏng hoặc định dạng lạ).")
    try:
        _, final_dur = await _probe(tmp)
        if final_dur < MIN_SEC:
            raise CloneError("Sau khi bỏ khoảng lặng, mẫu giọng còn quá ngắn hoặc gần như im lặng. "
                             "Hãy dùng đoạn 5–10 giây có người nói rõ.")
        tmp.replace(wav)
    finally:
        tmp.unlink(missing_ok=True)

    meta = {
        "id": clone_id, "name": name, "duration_sec": round(final_dur, 2),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "consent": True, "consent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    for k in ("consent_version", "consent_ip_hash"):          # bằng chứng đồng ý: phiên bản điều khoản + IP đã BĂM (không lưu IP thô)
        if audit and audit.get(k):
            meta[k] = str(audit[k])[:80]
    (udir / f"{clone_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"id": clone_id, "name": name, "created_at": meta["created_at"], "duration_sec": meta["duration_sec"]}

