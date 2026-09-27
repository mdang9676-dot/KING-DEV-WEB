# Third-Party Licenses

Dự án này sử dụng các thư viện/dependency bên thứ ba sau đây. Mỗi thư viện giữ
nguyên giấy phép gốc của nó; việc dùng dự án này không thay đổi các giấy phép đó.

| Package | License | Ghi chú |
|---|---|---|
| FastAPI | MIT | Web framework backend |
| Uvicorn | BSD-3-Clause | ASGI server |
| SQLAlchemy | MIT | ORM |
| Pydantic | MIT | Validation |
| faster-whisper | MIT | Speech-to-text |
| yt-dlp | Unlicense | Video downloader (chỉ dùng cho nguồn công khai được phép) |
| edge-tts | LGPL-3.0 | Text-to-speech |
| httpx | BSD-3-Clause | HTTP client (gọi API dịch) |
| Argos Translate | MIT (core); từng gói ngôn ngữ có thể khác | Dịch máy offline |
| FFmpeg (binary hệ thống) | LGPL/GPL tùy build | Xử lý media - gọi qua subprocess, không nhúng mã nguồn |
| Redis | BSD-3-Clause | Cache/queue (tùy chọn) |
| Next.js / React (nếu build thêm frontend nâng cao) | MIT | Frontend framework |
| Tailwind CSS | MIT | CSS framework |

**Lưu ý quan trọng:** đây là danh sách dựa trên kiến thức về các phiên bản phổ
biến của các thư viện này. Trước khi phát hành thương mại, hãy chạy công cụ như
`pip-licenses` hoặc `license-checker` để tự động xuất danh sách license chính
xác từ `requirements.txt` / `package.json` thực tế tại thời điểm build, vì
license của package có thể thay đổi giữa các phiên bản.

| cryptography | Apache-2.0 / BSD | Mã hoá API key (Fernet) |
| Pillow | HPND | Xử lý ảnh (OCR) |
| pytesseract / Tesseract | Apache-2.0 | OCR |
| Playwright (chỉ dùng kiểm thử giao diện) | Apache-2.0 | Không đóng gói trong sản phẩm |
| VieNeu-TTS (`vieneu`, tuỳ chọn — `requirements-voice.txt`) | Apache-2.0 (mã nguồn + model v3 Turbo; đã đối chiếu README, `pyproject.toml` và model card ngày 21/09/2026) | TTS tiếng Việt + nhân bản giọng, tự host. **Chỉ bản mã nguồn mở v3 Turbo/Nano; bản v4 là đóng nguồn, không dùng.** |
| MOSS-Audio-Tokenizer-Nano (codec của VieNeu v3) | Apache-2.0 (theo README của ZeroTTS, chưa tự đối chiếu repo gốc) | Được `vieneu` tải về từ Hugging Face |
| sea-g2p, onnxruntime, soundfile, soxr, kaldi-native-fbank, gradio, librosa (phụ thuộc của `vieneu`) | Chưa đối chiếu từng cái | Chạy `pip-licenses` sau khi cài `requirements-voice.txt` |
