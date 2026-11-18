"""ที่เก็บไฟล์ต้นฉบับ: เขียนครั้งเดียว ตรวจ hash ได้ แยกสาธารณะ/ภายใน และเปิดผ่าน API ตามสิทธิ์"""
import os
import stat

import pytest

from app.storage import RawStore, sha256_bytes


def test_store_is_content_addressed_and_write_once(tmp_path):
    store = RawStore(tmp_path / "raw")
    src = tmp_path / "ระเบียบ.pdf"
    src.write_bytes(b"%PDF-1.4 test")
    a = store.put_file(src, "public")
    assert a.created and a.sha256 == sha256_bytes(b"%PDF-1.4 test")
    assert a.rel_path == f"public/{a.sha256[:2]}/{a.sha256}.pdf"
    p = store.path(a.rel_path)
    assert not (p.stat().st_mode & stat.S_IWUSR)  # อ่านอย่างเดียว
    b = store.put_file(src, "public")  # ส่งซ้ำ → ไม่เขียนใหม่
    assert not b.created and b.rel_path == a.rel_path
    assert store.verify(a.rel_path, a.sha256) == "ok"
    os.chmod(p, 0o644)
    p.write_bytes(b"tampered")
    assert store.verify(a.rel_path, a.sha256) == "mismatch"
    with pytest.raises(ValueError):
        store.path("../outside.pdf")
    with pytest.raises(ValueError):
        store.put_file(src, "secret")


def test_raw_api_permissions(tmp_path):
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash",
                      RAW_STORE_DIR=str(tmp_path / "raw"))
    os.environ.pop("REGISTER_XLSX", None)
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    from app.seed.raw_files import add_file, verify_all
    pub, internal = tmp_path / "law.pdf", tmp_path / "wi.pdf"
    pub.write_bytes(b"%PDF public law")
    internal.write_bytes(b"%PDF internal manual")
    with get_engine().begin() as conn:
        seed.run(conn)
        conn.execute(text("DELETE FROM raw_file"))
        p = add_file(conn, pub, "public", source="ราชกิจจานุเบกษา")
        i = add_file(conn, internal, "internal", source="สำนักงาน กกพ.")
        conn.execute(text("INSERT INTO raw_file_link VALUES (:s, 'eli/th/reg/erc/2552/appeal', 'text', NULL) "
                          "ON CONFLICT DO NOTHING"), {"s": p["sha256"]})
        assert [r["status"] for r in verify_all(conn)] == ["ok", "ok"]
    c = TestClient(app)
    d = c.get("/api/v1/laws/provisions/eli/th/reg/erc/2552/appeal/clause-9", headers={"X-Demo-User": "pub"}).json()
    files = {f["sha256"]: f for f in d["source_files"]}
    assert files[p["sha256"]]["available"] is True
    assert any(not f["available"] for f in d["source_files"])  # ไฟล์จริงตาม JSON ยังไม่อยู่ในที่เก็บของเทสต์
    r = c.get(f"/api/v1/raw/{p['sha256']}", headers={"X-Demo-User": "pub"})
    assert r.status_code == 200 and r.content == b"%PDF public law"
    assert c.get(f"/api/v1/raw/{i['sha256']}", headers={"X-Demo-User": "pub"}).status_code == 404
    assert c.get(f"/api/v1/raw/{i['sha256']}", headers={"X-Demo-User": "lgl1"}).status_code == 200
    assert c.get("/api/v1/raw/not-a-hash", headers={"X-Demo-User": "lgl1"}).status_code == 400
    cov = c.get("/api/v1/laws/coverage", headers={"X-Demo-User": "lgl1"}).json()["raw"]
    assert {x["key"]: x["files"] for x in cov["by_class"]} == {"internal": 1, "public": 1}
    assert cov["missing"]  # ตัวบทอ้างไฟล์ที่ยังไม่ได้วางในที่เก็บ → แสดงเป็นรายการขาด


def test_add_zip_decodes_thai_names_without_extracting(tmp_path):
    """zip จาก Google Drive: ชื่อไทยไม่ติดธง UTF-8 และอาจยาวเกินระบบไฟล์ — เก็บได้โดยไม่แตกลงดิสก์ด้วยชื่อเดิม"""
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    import zipfile

    os.environ.update(DATABASE_URL=db, RAW_STORE_DIR=str(tmp_path / "raw"))
    from app.config import get_settings
    get_settings.cache_clear()
    from sqlalchemy import text

    from app.db import get_engine
    from app.seed.raw_files import add_zip
    long_name = "01.1-01.52/1.40.3/" + "ประกาศ กกพ รับซื้อไฟฟ้า ฉุกเฉิน " * 6 + "65ฉบับ4.pdf"
    zp = tmp_path / "drive.zip"
    class DriveInfo(zipfile.ZipInfo):  # แบบที่ Drive เขียน: ชื่อเป็นไบต์ UTF-8 แต่ไม่ตั้งธง UTF-8
        def _encodeFilenameFlags(self):
            return self.filename.encode("utf-8"), self.flag_bits & ~0x800

    with zipfile.ZipFile(zp, "w") as z:
        z.writestr(DriveInfo(long_name), b"%PDF test")
        z.writestr("notes.txt", b"ignored")
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM raw_file"))
        res = add_zip(conn, zp, "public", received_from="ฝ่ายกฎหมาย")
        again = add_zip(conn, zp, "public")
        row = conn.execute(text("SELECT original_name, note FROM raw_file")).mappings().one()
    assert len(res) == 1 and res[0]["new_file"] and not again[0]["new_file"]
    assert row["original_name"].endswith("65ฉบับ4.pdf") and "1.40.3" in row["note"]
