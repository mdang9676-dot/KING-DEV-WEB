FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng fonts-noto-core fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements-voice.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Giọng VieNeu + nhân bản giọng (tuỳ chọn, nặng hơn): docker compose build --build-arg WITH_VOICE=1
ARG WITH_VOICE=0
RUN if [ "$WITH_VOICE" = "1" ]; then pip install --no-cache-dir -r requirements-voice.txt; fi
# cache model Hugging Face nằm trong volume data => không phải tải lại mỗi lần dựng container
ENV HF_HOME=/app/data/hf
COPY backend/app ./app
COPY frontend ./frontend
ENV FRONTEND_DIR=/app/frontend
RUN useradd -m app && mkdir -p data && chown -R app /app
USER app
EXPOSE 8000
# 1 worker: hàng đợi xuất video nằm trong RAM
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
