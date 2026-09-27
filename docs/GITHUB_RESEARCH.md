# GitHub / Open-Source Research

Bảng dưới liệt kê các công cụ/thư viện open-source được nghiên cứu và (một phần)
sử dụng trong dự án này. Không có mã nguồn nào bị copy nguyên khối — mỗi thư viện
được dùng qua interface/dependency chính thức (pip install), phần code tích hợp
(providers, wrappers) là code tự viết cho dự án.

| Repository / Thư viện | License | Chức năng | Mức sử dụng trong dự án | Rủi ro license | Lý do chọn |
|---|---|---|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | Speech-to-text (Whisper tối ưu bằng CTranslate2) | Dùng trực tiếp qua pip, wrap trong `whisper_service.py` | Thấp - MIT permissive | Nhanh hơn openai-whisper gốc nhiều lần trên CPU, license permissive |
| [openai/whisper](https://github.com/openai/whisper) | MIT | Speech-to-text gốc | Không dùng trực tiếp (đã thay bằng faster-whisper) | Thấp | Chỉ tham khảo kiến trúc |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense (public domain) | Tải video từ YouTube/Vimeo... | Dùng trực tiếp qua pip trong `url_import_service.py`, chỉ gọi cho domain trong allowlist | Rất thấp | Chuẩn công nghiệp, license cực kỳ permissive, hỗ trợ metadata-only mode |
| [edge-tts](https://github.com/rany2/edge-tts) | LGPL-3.0 | Text-to-speech miễn phí dùng dịch vụ Edge của Microsoft | Dùng qua pip trong `tts_providers.py` (dynamic import, không static-link vào binary phân phối) | Trung bình - LGPL yêu cầu cho phép thay thế thư viện; vì dùng qua pip package riêng biệt (không sửa/nhúng mã nguồn) nên tuân thủ | Miễn phí, không cần API key, chất lượng tốt |
| [Piper](https://github.com/rhasspy/piper) | MIT | TTS local/offline, chạy trên CPU yếu | Optional provider trong `tts_providers.py`, cần tải model .onnx riêng | Thấp - MIT | Chạy hoàn toàn offline, phù hợp yêu cầu privacy |
| [Kokoro TTS](https://github.com/hexgrad/kokoro) | Apache-2.0 | TTS chất lượng cao, nhẹ | Chưa tích hợp trong MVP này (để lại interface `VoiceProvider` sẵn cho việc thêm sau) | Thấp | Cần thêm work để tích hợp, đánh dấu roadmap |
| [Coqui XTTS](https://github.com/coqui-ai/TTS) và bản tinh chỉnh tiếng Việt (viXTTS…) | **Chưa tra cứu lại.** Theo trí nhớ: mã nguồn MPL-2.0 nhưng *trọng số* XTTS-v2 theo Coqui Public Model License (hạn chế thương mại) — cần tự đối chiếu | Voice cloning TTS đa ngôn ngữ | Không tích hợp (VieNeu đáp ứng tốt hơn, license rõ hơn) | Cao nếu dùng thương mại | Thua ZeroTTS/VieNeu về WER tiếng Việt trong benchmark của ZeroTTS (16–18% so với 1–4%) |
| Chatterbox TTS | Chưa tra cứu, chưa xác nhận hỗ trợ tiếng Việt | Voice cloning | Không tích hợp | Chưa đánh giá | — |
| Qwen-TTS | Chưa tra cứu | TTS | Chưa tích hợp | Chưa đánh giá | Để roadmap |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Apache-2.0 | OCR phát hiện chữ trong khung hình | Chưa tích hợp trong MVP (dùng cho remove-subtitle nâng cao). Interface OCR có thể thêm sau | Thấp | Apache-2.0 permissive, hỗ trợ tiếng Việt tốt |
| [EasyOCR](https://github.com/JaidedAI/EasyOCR) | Apache-2.0 | OCR đa ngôn ngữ | Chưa tích hợp | Thấp | Backup nếu PaddleOCR không phù hợp |
| [video-subtitle-extractor](https://github.com/YaoFANGUK/video-subtitle-extractor) (YaoFANGUK) | Apache-2.0 | Trích phụ đề cứng, tự phát hiện vùng phụ đề, lọc logo/watermark | CHỈ tham khảo ý tưởng (tự phát hiện vùng + lọc chữ tĩnh). KHÔNG copy code; `region_detection.py` là bản tự cài đặt bằng thống kê vị trí chữ qua nhiều frame | Thấp (chỉ ý tưởng); nếu sau này dùng trực tiếp code/model của họ phải giữ thông báo Apache-2.0 | Là dự án mã nguồn mở phổ biến cùng bài toán; họ dùng deep learning để phát hiện vùng, bản của ta dùng thống kê nên nhẹ hơn nhưng kém chắc hơn |
| [Argos Translate](https://github.com/argosopentech/argos-translate) | MIT/CC0 hỗn hợp (kiểm tra từng gói ngôn ngữ) | Dịch máy local/offline | Dùng optional trong `translation_providers.py` như `ArgosLocalProvider` | Thấp-Trung bình - một số gói ngôn ngữ có license riêng | Cho phép dịch mà không cần API key trả phí |
| FFmpeg | LGPL/GPL tùy build | Xử lý video/audio (extract audio, burn subtitle, mix audio, blur) | Dùng qua subprocess (gọi binary có sẵn trên hệ thống, KHÔNG link tĩnh vào code) | Thấp khi dùng qua subprocess độc lập (không static-link) | Bắt buộc, chuẩn công nghiệp cho xử lý media |

## Các dự án tham khảo ý tưởng kiến trúc (không copy code)

| Dự án | Ghi chú |
|---|---|
| videotrans | Tham khảo pipeline tổng thể video → transcribe → translate → dub → render |
| Whisper Studio (các bản mã nguồn mở tương tự) | Tham khảo UI/UX cho subtitle editor |
| vdub / kekedubing / Dublaro | Tham khảo khái niệm dubbing pipeline, không truy cập/copy source code độc quyền nào |

## Nguyên tắc tuân thủ được áp dụng

1. Không copy-paste nguyên khối code từ bất kỳ repo nào — mọi tích hợp qua `pip install` package chính thức + code wrapper tự viết.
2. Ưu tiên MIT/Apache-2.0/BSD/Unlicense khi có nhiều lựa chọn tương đương.
3. Với các thư viện GPL/LGPL (edge-tts, FFmpeg tùy build): dùng qua dynamic import / subprocess độc lập, không nhúng/sửa đổi source rồi phân phối lại dưới tên khác.
4. Voice cloning: chỉ bật khi người vận hành cài `requirements-voice.txt`; server bắt buộc xác nhận quyền sử dụng giọng, chỉ dùng engine có license xác nhận được (hiện là VieNeu-TTS, Apache-2.0). Xem mục tra cứu ngày 21/09/2026 ở trên.
5. Tất cả license bên thứ ba được liệt kê chi tiết tại `THIRD_PARTY_LICENSES.md`.

## Voice cloning tiếng Việt, tự host — tra cứu trực tiếp ngày 21/09/2026

Đã đọc README/model card của từng repo (không dựa vào trí nhớ), trừ chỗ ghi chú khác.

| Engine | License | Tiếng Việt | Nhân bản giọng thật sự? | Chạy ở đâu | Kết luận |
|---|---|---|---|---|---|
| [VieNeu-TTS v3 Turbo](https://github.com/pnnbao97/VieNeu-TTS) | **Apache-2.0** (README, `pyproject.toml`, model card HF) | Bản địa; có nhãn 3 miền, ~23 giọng dựng sẵn | **Có** — mẫu 3–8 giây, tự khử nhiễu | CPU bằng ONNX Runtime (không cần PyTorch), 48 kHz; GPU tuỳ chọn | **ĐÃ TÍCH HỢP** (`VieNeuProvider`). Lưu ý: v4 (giống hơn) là đóng nguồn, chỉ qua API của tác giả |
| [ZeroTTS](https://github.com/zeroweight-ai/ZeroTTS) | MIT (mã + trọng số) | Bản địa, WER thấp nhất trong bảng của họ | **Không** trong bản phát hành này — README nói rõ *voice encoder chưa công bố*, chỉ có 8 giọng dựng sẵn | CPU ONNX, ~0,5× thời gian thực, ~900 MB | Không tích hợp (không nhân bản được). Có thể thêm sau như nguồn giọng dựng sẵn |
| [Valtec-TTS](https://github.com/tronghieuit/valtec-tts) | **CC BY-NC 4.0** — chỉ phi thương mại | Bản địa | Có (mẫu 3–10 giây) | CPU | **Loại** — không phù hợp nếu bạn bán/cho thuê dịch vụ |
| [OmniVoice](https://github.com/k2-fsa/OmniVoice) (k2-fsa) | Apache-2.0 theo các nguồn thứ ba tôi đọc — **chưa mở file LICENSE của repo để xác nhận** | 600+ ngôn ngữ, gồm tiếng Việt | Có | Nặng (Qwen3-0.6B + codec), khuyến nghị GPU; ZeroTTS đo RTF 6× trên CPU | Ứng viên nếu bạn có GPU. Các bản *tinh chỉnh tiếng Việt* của cộng đồng có thể dùng dữ liệu CC-BY-NC-SA (thấy ít nhất một) — phải kiểm từng bản |

Phụ thuộc gián tiếp cần đối chiếu trước khi thương mại hoá: `sea-g2p`, `gradio`, `librosa`, `soxr`, `kaldi-native-fbank` (xem `THIRD_PARTY_LICENSES.md`).

**Vì sao chọn VieNeu**: là lựa chọn duy nhất đồng thời (1) license Apache-2.0 xác nhận được, (2) tiếng Việt bản địa, (3) nhân bản giọng thật sự có trong bản mở, (4) chạy được trên CPU thường. Nhược điểm cần biết: nhân bản v3 Turbo chưa bằng v4 đóng nguồn; tôi chưa nghe chất lượng thực tế vì môi trường xây dựng không có mạng.

**Ràng buộc đã áp dụng cho nhân bản giọng**: bắt buộc xác nhận quyền dùng giọng ở server (không chỉ ở UI), lưu thời điểm xác nhận, không có giọng người nổi tiếng dựng sẵn, mẫu chỉ thuộc về chủ tải lên.

## Trạng thái tại thời điểm bàn giao

Do môi trường build không có kết nối internet, danh sách trên dựa trên kiến thức đã biết về các dự án này tính đến thời điểm huấn luyện, KHÔNG phải kết quả tra cứu trực tiếp GitHub tại thời điểm giao dự án. **Trước khi dùng cho production/thương mại, bạn nên tự kiểm tra lại license mới nhất của từng repo** (license có thể đổi giữa các phiên bản).
