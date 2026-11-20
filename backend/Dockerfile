FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/home/app
# นำเข้าเอกสารจากหน้าเว็บ: pdftotext (poppler) และ OCR ภาษาไทยในเครื่อง (Tesseract tha) — ไม่ส่งไฟล์ออกนอกเซิร์ฟเวอร์
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-tha \
    && rm -rf /var/lib/apt/lists/*
# ไลบรารีตามไฟล์ล็อก (ตรวจ hash ทุกแพ็กเกจ)
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt
# OCR engine ทางเลือก (ไม่บังคับ): AksonOCR-Lite / runtime ของ OpenThai2.0-OCR-Text — ดู .env.example (OCR_ENGINE)
# ระบุ commit/รุ่นให้แน่นอน เช่น https://github.com/Remtir/AksonOCR-Lite/archive/<commit>.zip
ARG OCR_PIP=""
RUN if [ -n "$OCR_PIP" ]; then pip install $OCR_PIP; fi
# โหมดพัฒนา (docker-compose.yml): ติดตั้งเครื่องมือทดสอบด้วย — image ระบบจริงไม่มี
ARG INSTALL_DEV=0
COPY requirements-dev.txt .
RUN if [ "$INSTALL_DEV" = "1" ]; then pip install --require-hashes -r requirements-dev.txt; fi
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY tools ./tools
COPY eval ./eval
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# ทำงานด้วยผู้ใช้ที่ไม่ใช่ root; เขียนได้เฉพาะ /data/raw (คลังไฟล์ต้นฉบับ) และ /tmp
RUN useradd --uid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /data/raw && chown -R app:app /data/raw \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["docker-entrypoint.sh"]
# 1 worker: งานเบื้องหลัง (สร้าง embedding index, OCR) และ cache ค่าตั้งอยู่ใน process เดียว
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
