"""ตรวจไฟล์ที่อัปโหลดก่อนเก็บลงคลัง: ชนิดไฟล์จากเนื้อไฟล์, เนื้อหาที่สั่งงานได้ (macro / JavaScript), zip bomb,
จำนวนหน้า และสแกนไวรัสด้วย ClamAV (clamd) — ไม่ผ่านข้อใดข้อหนึ่ง ไฟล์ไม่ถูกเก็บ

AV_SCAN=off | optional | required   (ระบบจริงควรเป็น required: clamd ไม่พร้อม = ไม่รับไฟล์)
CLAMD_HOST / CLAMD_PORT (ค่าเริ่ม clamav:3310), CLAMD_TIMEOUT_S
"""
import io
import logging
import os
import re
import socket
import struct
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass

log = logging.getLogger("erc.filecheck")

MAX_PDF_PAGES = int(os.environ.get("UPLOAD_MAX_PDF_PAGES", "1000"))
MAX_UNZIPPED = 200 * 1024 * 1024     # docx: ขนาดรวมหลังแตกไฟล์
MAX_ZIP_ENTRIES = 5000
MAX_RATIO = 200                      # อัตราส่วนบีบอัดต่อไฟล์ย่อย (zip bomb มักเป็นหลักพัน)
PDF_ACTIVE = re.compile(rb"/(?:JavaScript|JS|Launch)(?=[\s/<(\[])")   # ไม่เห็นใน object stream ที่บีบอัด — ClamAV ตรวจซ้ำ
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
VBA_MARKERS = ("_VBA_PROJECT".encode("utf-16-le"), "Macros".encode("utf-16-le"), b"vbaProject.bin")


class FileRejected(ValueError):
    def __init__(self, reason: str, status: int = 400, signature: str | None = None):
        super().__init__(reason)
        self.status, self.signature = status, signature


class AvUnavailable(RuntimeError):
    pass


@dataclass
class CheckResult:
    pages: int | None
    av: str          # clean | skipped | unavailable


# ---------------------------------------------------------------- ชนิดไฟล์และเนื้อหา
def _check_pdf(data: bytes) -> int:
    if b"%PDF-" not in data[:1024]:
        raise FileRejected("เนื้อไฟล์ไม่ใช่ PDF")
    if b"%%EOF" not in data[-4096:]:
        raise FileRejected("ไฟล์ PDF ไม่สมบูรณ์ (อัปโหลดไม่ครบหรือไฟล์เสีย)")
    if PDF_ACTIVE.search(data):
        raise FileRejected("ไฟล์ PDF มีสคริปต์หรือคำสั่งเปิดโปรแกรม (JavaScript/Launch) — บันทึกเป็น PDF ใหม่แล้วนำเข้าอีกครั้ง")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        info = subprocess.run(["pdfinfo", f.name], capture_output=True, text=True, timeout=60)
    if info.returncode != 0:
        raise FileRejected("อ่านไฟล์ PDF ไม่ได้ (ไฟล์เสียหรือรูปแบบไม่ถูกต้อง)")
    if re.search(r"^Encrypted:\s+yes", info.stdout, flags=re.M):
        raise FileRejected("ไฟล์ PDF ถูกเข้ารหัส/ล็อกรหัสผ่าน — ปลดล็อกก่อนนำเข้า")
    m = re.search(r"^Pages:\s+(\d+)", info.stdout, flags=re.M)
    pages = int(m.group(1)) if m else 0
    if pages > MAX_PDF_PAGES:
        raise FileRejected(f"ไฟล์มี {pages} หน้า เกินกำหนด {MAX_PDF_PAGES} หน้า — แยกไฟล์ก่อนนำเข้า", 413)
    return pages


def _check_docx(data: bytes) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise FileRejected("เนื้อไฟล์ไม่ใช่ .docx") from e
    infos = z.infolist()
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise FileRejected("เนื้อไฟล์ไม่ใช่เอกสาร Word (.docx)")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise FileRejected("ไฟล์มี macro (VBA) — บันทึกเป็น .docx ธรรมดาแล้วนำเข้าอีกครั้ง")
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise FileRejected("ไฟล์ .docx มีขนาดหลังแตกไฟล์ใหญ่ผิดปกติ", 413)
    for i in infos:
        if i.compress_size and i.file_size / i.compress_size > MAX_RATIO and i.file_size > 10 * 1024 * 1024:
            raise FileRejected("ไฟล์ .docx มีอัตราการบีบอัดผิดปกติ (อาจเป็น zip bomb)", 413)
        if i.filename.startswith("/") or ".." in i.filename.split("/"):
            raise FileRejected("ไฟล์ .docx มีชื่อไฟล์ย่อยผิดรูปแบบ")


def _check_doc(data: bytes) -> None:
    if not data.startswith(OLE_MAGIC):
        raise FileRejected("เนื้อไฟล์ไม่ใช่เอกสาร Word (.doc)")
    if any(m in data for m in VBA_MARKERS):
        raise FileRejected("ไฟล์มี macro (VBA) — บันทึกเป็น .docx ธรรมดาแล้วนำเข้าอีกครั้ง")


# ---------------------------------------------------------------- ClamAV
def av_mode() -> str:
    m = os.environ.get("AV_SCAN", "off").strip().lower()
    return m if m in ("off", "optional", "required") else "required"


def clamd_scan(data: bytes) -> str | None:
    """ส่งไฟล์ให้ clamd ด้วยคำสั่ง INSTREAM → None ถ้าไม่พบ, ชื่อมัลแวร์ถ้าพบ (AvUnavailable ถ้าติดต่อไม่ได้)"""
    host, port = os.environ.get("CLAMD_HOST", "clamav"), int(os.environ.get("CLAMD_PORT", "3310"))
    try:
        with socket.create_connection((host, port), timeout=float(os.environ.get("CLAMD_TIMEOUT_S", "60"))) as s:
            s.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 1 << 16):
                chunk = data[i:i + (1 << 16)]
                s.sendall(struct.pack(">I", len(chunk)) + chunk)
            s.sendall(struct.pack(">I", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = s.recv(4096)
                if not part:
                    break
                reply += part
    except OSError as e:
        raise AvUnavailable(f"ติดต่อ clamd ที่ {host}:{port} ไม่ได้: {e}") from e
    text = reply.rstrip(b"\0").decode("utf-8", "replace").strip()
    if text.endswith("OK"):
        return None
    if text.endswith("FOUND"):
        return text.split(":", 1)[-1].strip()[: -len("FOUND")].strip() or "unknown"
    raise AvUnavailable(f"clamd ตอบผิดรูปแบบ: {text[:200]}")   # เช่น size limit exceeded


def check(data: bytes, ext: str) -> CheckResult:
    """ตรวจไฟล์ทั้งหมด — FileRejected ถ้าไม่รับ"""
    pages = None
    if ext == ".pdf":
        pages = _check_pdf(data)
    elif ext == ".docx":
        _check_docx(data)
    elif ext == ".doc":
        _check_doc(data)
    else:
        raise FileRejected("รองรับเฉพาะไฟล์ .pdf .docx .doc")
    mode = av_mode()
    if mode == "off":
        return CheckResult(pages, "skipped")
    try:
        found = clamd_scan(data)
    except AvUnavailable as e:
        if mode == "required":
            log.error("virus scan unavailable: %s", e)
            raise FileRejected("ระบบตรวจไวรัสไม่พร้อม จึงยังรับไฟล์ไม่ได้ — แจ้งผู้ดูแลระบบ", 503) from e
        log.warning("virus scan skipped (optional): %s", e)
        return CheckResult(pages, "unavailable")
    if found:
        raise FileRejected(f"ตรวจพบมัลแวร์ ({found}) — ไม่รับไฟล์นี้", 422, signature=found)
    return CheckResult(pages, "clean")
