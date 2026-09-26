# KINGDEV TOOL — Dịch và lồng tiếng video

Web tự host: tải video (file hoặc link), tự dịch phụ đề, che/xoá phụ đề & logo cũ, thêm logo riêng, lồng tiếng, nâng độ nét, rồi tải video về. Việc xuất video **chạy ngầm trên máy chủ** (đóng trang vẫn chạy, mở lại vẫn thấy tiến trình).

## Chạy nhanh
Cần: Python 3.11+, **FFmpeg ≥ 4.4** (`ffmpeg -version`), Tesseract (chỉ khi dùng OCR: `apt install tesseract-ocr tesseract-ocr-vie`).
```bash
cd backend
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # điền APP_SECRET
uvicorn app.main:app --host 0.0.0.0 --port 8000       # chạy 1 worker
```
Mở **http://localhost:8000** (backend phục vụ luôn giao diện). Docker: `docker compose up --build`.
Chạy test: `cd backend && pytest tests -v`.
Kiểm tra máy trước khi chạy lần đầu: `python backend/scripts/doctor.py` (thêm `--net` để thử kết nối tới Hugging Face/Gemini/OpenAI). Nó báo thiếu FFmpeg, bộ lọc, font tiếng Việt, gói Python… kèm cách sửa.

## Cách dùng
1. **Nguồn video**: UPVIDEO → Xác nhận, hoặc dán link → Xác nhận; có thanh tiến trình. Hỗ trợ **mọi trang mà yt-dlp có trình tải riêng** (hơn 1000 trang: YouTube, TikTok, Facebook, X, Instagram, Bilibili, Vimeo, Dailymotion, Twitch…) và **link file video/âm thanh trực tiếp** (.mp4, .webm, .mp3…).
2. **Kết nối AI**: dán API key → tự nhận diện nhà cung cấp (nếu không chắc sẽ hỏi bạn chọn) → Kiểm tra key & liệt kê model → Lưu. Hỗ trợ Gemini, OpenAI, Claude, DeepSeek, Groq, OpenRouter, xAI, Mistral, Together, Fireworks, NVIDIA, Cerebras. Model rẻ được chọn sẵn; dịch theo lô để tiết kiệm token.
3. **Cấu hình video**: độ phân giải, độ nét, giảm nhiễu → Xác nhận.
4. **Phụ đề**: nguồn chữ (OCR hoặc Whisper), ngôn ngữ, chế độ phụ đề cũ **Che** (làm mờ) / **Xoá** (nội suy nền) / **Đè** (phủ nền đặc). Vùng phụ đề: tự tìm hoặc tự vẽ trên màn hình chiếu.
   *Nâng cao*: font tiếng Việt (thêm font riêng vào `data/fonts`), engine OCR (Tesseract/RapidOCR/PaddleOCR — chỉ bật cái đã cài), **xưng hô** (hiện đại / cổ trang–kiếm hiệp / anime), **từ điển tên riêng** (`gốc = dịch`), **rút gọn dòng quá dài** theo ký tự/giây. Khi lồng tiếng tiếng Việt, số/tiền/%/giờ/ngày được đọc thành chữ tự động. Video ≥ 30 phút có cảnh báo trước.
5. **Logo**: “Chọn logo trên màn hình” → bấm/kéo khung quanh logo → bấm **✕** để xoá. “Tải logo” → tự xoá phông nền (chỉnh độ nhạy) → kéo/đổi cỡ trên màn hình chiếu; vị trí được giữ khi xuất.
6. **Giọng**: Edge TTS (miễn phí), Gemini TTS (cần key Gemini) hoặc **VieNeu (tự host, có nhân bản giọng — xem mục dưới)**; “Nghe thử” đọc cụm *KINGWEB DEV*. Câu dài được tự tăng tốc để khớp thời gian.
   *Khớp thời gian*: **Tự nhiên** (mượn khoảng lặng phía sau nên ít phải đọc nhanh, mặc định) hoặc **Sát khung hình** (tăng tốc để vừa từng câu). Nguồn chữ *Whisper* cho chọn cỡ model (tiny → large-v3; chạy ngay trên máy chủ, lần đầu sẽ tải model).
7. **Xuất video** → thanh tiến trình → Tải video / Tải SRT.

## Nhân bản giọng (VieNeu-TTS, tự host)
Chọn nguồn giọng **VieNeu** → khung “Nhân bản giọng của bạn” hiện ra: đặt tên, tải file ghi âm **5–10 giây** (WAV/MP3/M4A/OGG/FLAC/WebM), **tích xác nhận quyền dùng giọng**, bấm *Tạo giọng nhân bản*. Giọng mới xuất hiện đầu danh sách (🎙), bấm *Nghe thử* rồi dùng để lồng tiếng như mọi giọng khác. VieNeu còn có sẵn ~20 giọng tiếng Việt (Bắc/Trung/Nam) không cần mẫu.

Cài đặt (tuỳ chọn, chạy CPU bằng ONNX, không cần PyTorch):
```bash
cd backend && pip install -r requirements-voice.txt     # Docker: WITH_VOICE=1 docker compose up --build
# .env: VIENEU_MODE=v3turbo (mặc định, 48 kHz) | v3nano (máy yếu, chất lượng thấp hơn); VIENEU_PRELOAD=true để nạp model lúc khởi động
```
Lần đầu dùng, model được tải từ Hugging Face (cần mạng một lần) và nạp mất vài chục giây; sau đó chạy offline.

Cách hoạt động & giới hạn:
- Mẫu giọng được chuẩn hoá (mono 44,1 kHz, bỏ khoảng lặng đầu, tối đa 12 giây) và lưu ở `data/voices/<user>/`; **không bị dọn tự động**, chỉ chính chủ nghe/dùng/xoá được. Xoá giọng là xoá hẳn file (gồm cả bản nghe thử và giọng đã nạp trong RAM của engine). Tắt hẳn tính năng bằng `VOICE_CLONING_ENABLED=false` (người dùng vẫn xoá được mẫu đã có). Mỗi mẫu lưu kèm phiên bản điều khoản + IP đã băm (không lưu IP thô).
- Server **bắt buộc** cờ xác nhận quyền sử dụng giọng khi tạo mẫu và lưu thời điểm xác nhận cùng mẫu. Không có giọng người nổi tiếng dựng sẵn. Bạn chịu trách nhiệm về quyền sử dụng giọng của người khác — đừng dùng để mạo danh hay lừa đảo.
- Chất lượng nhân bản phụ thuộc mẫu: ghi âm sạch, một người nói, không nhạc nền. Giọng lạ/đặc biệt có thể không giống hoàn toàn.
- Synth chạy tuần tự trên một engine dùng chung (an toàn, nhưng video dài sẽ lâu hơn Edge TTS). `int8` nhanh hơn ~1,6× nhưng chỉ đúng trên CPU có VNNI.
- Bản VieNeu-TTS v4 (nhân bản giống hơn) là **đóng nguồn/chỉ qua API của tác giả**; dự án này chỉ dùng bản mã nguồn mở v3 Turbo (Apache-2.0).

## Bảo mật đã làm
Mật khẩu băm scrypt · phiên JWT trong cookie httpOnly + SameSite · giới hạn thử đăng nhập · mọi API cần đăng nhập và **kiểm tra chủ sở hữu** video/job · API key AI được **mã hoá (Fernet)**, không trả lại đầy đủ cho trình duyệt · chỉ gọi tới nhà cung cấp AI trong danh sách cố định (không SSRF) · link được kiểm chính sách trước khi tải (chỉ http/https cổng 80/443; tên miền phải phân giải ra **toàn địa chỉ công khai**, chặn mạng nội bộ/metadata đám mây; bộ tải file trực tiếp kiểm lại ở mỗi lần kết nối và mỗi lần redirect, tối đa 3; mỗi người tải tối đa 2 link cùng lúc) · logo kiểm tra bằng chữ ký file · mọi tuỳ chọn được validate chặt trước khi vào lệnh FFmpeg · văn bản OCR bị lọc thẻ ASS độc hại · video kết quả chỉ tải qua endpoint có xác thực.
Đặt `APP_SECRET` mạnh; khi deploy dùng HTTPS và `TRUST_PROXY=true` nếu sau proxy.

## Đã kiểm thử / chưa kiểm thử (nói thẳng)
- **Đã chạy thật**: dựng video bằng FFmpeg (4 kiểu che, xoá, nâng nét, phụ đề mới, logo, trộn tiếng, tiến trình %), lồng tiếng khớp thời gian, phát hiện vùng phụ đề, nhận diện key AI, dịch theo lô, allowlist link, xoá phông logo, và **toàn bộ giao diện trong Chromium** (với API giả).
- **Nhân bản giọng (VieNeu)**: đã chạy thật phần *của dự án* — lưu/kiểm tra mẫu giọng bằng FFmpeg (bắt buộc xác nhận, cắt còn ≤ 12 giây, chặn file hỏng/im lặng/quá ngắn, cô lập theo người dùng, chống path traversal) và ghép giọng 48 kHz vào timeline lồng tiếng, với **engine giả** (`tests/test_vieneu_provider.py`, `tests/test_voice_clones.py`). **Chưa chạy với model VieNeu thật**: môi trường tạo bản này không có mạng nên chưa cài được `vieneu`, chưa nghe thử chất lượng giọng nhân bản, chưa kiểm tra tên tham số/định dạng trả về của SDK trên phiên bản bạn cài (code viết theo README chính thức của VieNeu, xử lý dự phòng vài chỗ). Lần đầu hãy thử: cài → tạo giọng từ mẫu ngắn → Nghe thử → xuất video ngắn.
- **Chưa chạy thật** (môi trường tạo dự án không có mạng/thư viện): tầng FastAPI + SQLAlchemy ghép lại, gọi API thật của Gemini/OpenAI/Edge TTS, tải link thật bằng yt-dlp, OCR Tesseract trên video thật. Lần chạy đầu hãy thử theo thứ tự: đăng ký → upload video ngắn → xuất chỉ với “Nâng chất lượng” → rồi mới bật phụ đề/lồng tiếng.

## Giới hạn
- **Tải từ link**: không hỗ trợ video riêng tư/cần đăng nhập/DRM (không cố vượt), HLS `.m3u8` trực tiếp, và trang không có trình tải riêng lẫn không phải link file (ví dụ trang blog nhúng video). Với nhánh yt-dlp, mạng do chính yt-dlp thực hiện nên ta chỉ kiểm được URL ban đầu — khi triển khai thật hãy thêm tường lửa chặn đường ra tới dải mạng nội bộ. Chỉ tải nội dung bạn có quyền sử dụng.
- Chưa có xác minh email và quên mật khẩu (cần SMTP).
- “Xoá” phụ đề/logo dùng nội suy nền (delogo): sạch với vùng nhỏ, có vệt mờ với vùng lớn/nền phức tạp; không phải AI inpainting.
- Nâng độ nét = Lanczos + unsharp, không tạo thêm chi tiết (không phải AI siêu phân giải).
- Trình duyệt chỉ xem trước được MP4/H.264, WebM; MKV/AVI vẫn xử lý được nhưng có thể không phát trong khung xem.
- Gemini TTS bị giới hạn hạn mức theo gói của bạn (video dài có thể dính lỗi 429).
- Chỉ tải video bạn có quyền sử dụng; video riêng tư/DRM sẽ thất bại (không bypass).
- Chạy 1 worker; nếu máy chủ khởi động lại, job đang chạy được đánh dấu lỗi để chạy lại.
