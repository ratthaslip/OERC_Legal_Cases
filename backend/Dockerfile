FROM python:3.12-slim
WORKDIR /app
# นำเข้าเอกสารจากหน้าเว็บ: pdftotext (poppler) และ OCR ภาษาไทยในเครื่อง (Tesseract tha) — ไม่ส่งไฟล์ออกนอกเซิร์ฟเวอร์
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-tha \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# OCR engine ทางเลือก (ไม่บังคับ): AksonOCR-Lite / runtime ของ OpenThai2.0-OCR-Text — ดู .env.example (OCR_ENGINE)
ARG OCR_PIP=""
RUN if [ -n "$OCR_PIP" ]; then pip install --no-cache-dir $OCR_PIP; fi
COPY app ./app
COPY tests ./tests
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
