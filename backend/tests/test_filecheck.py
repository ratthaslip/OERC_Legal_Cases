"""ตรวจไฟล์อัปโหลด: ชนิดไฟล์จากเนื้อไฟล์, macro/สคริปต์, zip bomb, PDF เข้ารหัส/หน้าเกิน, ClamAV (clamd จำลอง)"""
import io
import socket
import struct
import threading
import zipfile

import pytest

from app import filecheck

EICAR_NAME = "Eicar-Test-Signature"


def _pdf(extra: bytes = b"") -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] " + extra + b">>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 4\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % x
    return out


def _docx(extra: dict | None = None, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


def test_valid_files_pass(monkeypatch):
    monkeypatch.setenv("AV_SCAN", "off")
    assert filecheck.check(_pdf(), ".pdf").pages == 1
    assert filecheck.check(_docx(), ".docx").av == "skipped"


@pytest.mark.parametrize("data,ext,msg", [
    (b"PK\x03\x04 not really", ".pdf", "ไม่ใช่ PDF"),
    (_pdf()[:-30], ".pdf", "ไม่สมบูรณ์"),
    (_pdf(b"/AA << /O << /S /JavaScript /JS (app.alert(1)) >> >> "), ".pdf", "สคริปต์"),
    (b"%PDF-1.4 not a zip", ".docx", "ไม่ใช่ .docx"),
    (_docx({"word/vbaProject.bin": "x"}), ".docx", "macro"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "_VBA_PROJECT".encode("utf-16-le"), ".doc", "macro"),
    (b"%PDF-1.4", ".doc", "ไม่ใช่เอกสาร Word"),
])
def test_rejections(monkeypatch, data, ext, msg):
    monkeypatch.setenv("AV_SCAN", "off")
    with pytest.raises(filecheck.FileRejected, match=msg):
        filecheck.check(data, ext)


def test_zip_bomb_rejected(monkeypatch):
    monkeypatch.setenv("AV_SCAN", "off")
    bomb = _docx({"word/media/big.bin": b"\0" * (60 * 1024 * 1024)})
    with pytest.raises(filecheck.FileRejected, match="zip bomb") as e:
        filecheck.check(bomb, ".docx")
    assert e.value.status == 413


def test_page_limit(monkeypatch):
    monkeypatch.setenv("AV_SCAN", "off")
    monkeypatch.setattr(filecheck, "MAX_PDF_PAGES", 0)
    with pytest.raises(filecheck.FileRejected, match="เกินกำหนด"):
        filecheck.check(_pdf(), ".pdf")


@pytest.fixture()
def fake_clamd(monkeypatch):
    """clamd จำลอง: ตอบ FOUND ถ้าเนื้อไฟล์มีคำว่า EICAR"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    seen = []

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            with c:
                buf = b""
                while not buf.endswith(b"\0"):
                    buf += c.recv(1)
                data = b""
                while True:
                    n = struct.unpack(">I", c.recv(4, socket.MSG_WAITALL))[0]
                    if n == 0:
                        break
                    data += c.recv(n, socket.MSG_WAITALL)
                seen.append(data)
                c.sendall(f"stream: {EICAR_NAME} FOUND\0".encode() if b"EICAR" in data else b"stream: OK\0")

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("CLAMD_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAMD_PORT", str(srv.getsockname()[1]))
    yield seen
    srv.close()


def test_clamd_clean_and_infected(monkeypatch, fake_clamd):
    monkeypatch.setenv("AV_SCAN", "required")
    assert filecheck.check(_pdf(), ".pdf").av == "clean"
    infected = _docx({"word/media/x.txt": "EICAR-STANDARD-ANTIVIRUS-TEST"}, zipfile.ZIP_STORED)
    with pytest.raises(filecheck.FileRejected) as e:
        filecheck.check(infected, ".docx")
    assert e.value.status == 422 and e.value.signature == EICAR_NAME
    assert len(fake_clamd) == 2 and fake_clamd[0] == _pdf()   # ส่งทั้งไฟล์ครบทุกไบต์


def test_clamd_down_required_vs_optional(monkeypatch):
    monkeypatch.setenv("CLAMD_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAMD_PORT", "1")          # ไม่มีบริการ
    monkeypatch.setenv("AV_SCAN", "required")
    with pytest.raises(filecheck.FileRejected) as e:
        filecheck.check(_pdf(), ".pdf")
    assert e.value.status == 503
    monkeypatch.setenv("AV_SCAN", "optional")
    assert filecheck.check(_pdf(), ".pdf").av == "unavailable"
