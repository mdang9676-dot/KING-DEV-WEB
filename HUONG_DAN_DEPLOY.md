# Hướng dẫn deploy KINGDEV TOOL lên Render (miễn phí phần khởi tạo, trả phí để chạy ổn)

## Vì sao không dùng gói Free của Render?
App này chạy Whisper + FFmpeg encode video → cần tối thiểu ~2GB RAM. Gói Free
(512MB, ngủ khi rảnh) sẽ bị crash hoặc bị tắt giữa chừng khi xử lý video.
File `render.yaml` đã đặt sẵn plan `starter` (rẻ nhất còn dùng được, ~7 USD/tháng).
Nếu muốn free, xem mục "Phương án free" ở cuối.

## Bước 1: Đẩy code lên GitHub
1. Vào https://github.com/new → tạo repo mới (đặt Private nếu không muốn công khai)
2. Trên máy m, giải nén file zip này ra một thư mục, rồi chạy:
   ```
   cd web_KING_DEV
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<ten-cua-m>/<ten-repo>.git
   git push -u origin main
   ```
   (Nếu chưa cài git: tải tại https://git-scm.com, hoặc dùng nút "upload files" trên web GitHub luôn cũng được.)

## Bước 2: Tạo Web Service trên Render
1. Vào https://dashboard.render.com → đăng nhập (hoặc đăng ký bằng GitHub cho nhanh)
2. Bấm **New +** → **Blueprint**
3. Chọn repo vừa push → Render tự đọc file `render.yaml` đã có sẵn trong zip này
4. Bấm **Apply** → Render tự tạo service, tự sinh `APP_SECRET`, tự gắn ổ đĩa lưu dữ liệu
5. Đợi build xong (~5-10 phút, vì phải cài ffmpeg + tesseract + các gói Python)

## Bước 3: Mở web
- Render cho m 1 link dạng `https://kingdev-tool.onrender.com`
- Mở link đó → vào thẳng giao diện app

## Bước 4: Kiểm tra
- Đăng ký tài khoản đầu tiên trong app
- Upload 1 video ngắn (~10-30 giây) → chỉ bật "Nâng chất lượng" trước, xem xuất video có chạy được không
- Sau đó mới thử bật phụ đề/dịch/lồng tiếng

## Nếu muốn bật nhân bản giọng (VieNeu)
Sửa trong `render.yaml`, thêm vào phần `envVars` của Docker build args, hoặc build
thủ công với `WITH_VOICE=1` — phần này nặng hơn, cân nhắc nâng plan lên mức RAM cao hơn.

## Phương án free (không khuyến khích, dễ lỗi)
- Railway.app: free 500 giờ/tháng, RAM khoảng 512MB-1GB tuỳ gói → vẫn có thể văng khi xử lý video dài
- Deploy: vào railway.app → New Project → Deploy from GitHub repo → Railway tự nhận Dockerfile
- Cần tự thêm biến môi trường `APP_SECRET` (copy giá trị bên dưới hoặc tự tạo bằng lệnh
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
- Volume lưu file: Railway cần bật "Volume" riêng, gắn vào `/app/data`, nếu không dữ liệu mất khi redeploy

## APP_SECRET dùng thử sẵn (nên đổi cái khác khi dùng thật)
```
SLcrjx-BhzrKNfBPs01TW_K7LZi8zuYoL8Hz19fpqXKtTpbSRwv-TJ6NHBDquVLY
```

## Nếu build lỗi
- Lỗi hết RAM lúc build/khi xử lý video → nâng plan
- Lỗi thiếu model Whisper lần đầu → bình thường, lần đầu chạy sẽ tự tải, cần đợi
- Video xuất bị "job lỗi" sau khi server restart → bình thường theo README, chạy lại job
