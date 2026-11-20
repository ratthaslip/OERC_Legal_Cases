"""PUBLIC_DATA_ONLY=true (เครื่องพัฒนาส่วนตัว/คลาวด์): ไม่มีตราสารภายใน คอลัมน์ภายในของทะเบียน ไฟล์ภายใน หรือเอกสารภายในจากหน้าเว็บ"""
import io
import json
import os

import pytest

from tests.test_internal_instruments import INTERNAL, write_internal_dir
from tests.test_register import workbook  # noqa: F401 — fixture


@pytest.fixture
def public_env(tmp_path, monkeypatch, workbook):  # noqa: F811
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.setenv("LLM_PROVIDER", "extractive")
    monkeypatch.setenv("EMBED_PROVIDER", "hash")
    monkeypatch.setenv("RAW_STORE_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("LAWS_INTERNAL_DIR", str(write_internal_dir(tmp_path / "laws-internal")))
    monkeypatch.setenv("REGISTER_XLSX", str(workbook))
    monkeypatch.setenv("REGISTER_PUBLIC_JSON", str(tmp_path / "register-public.json"))
    monkeypatch.setenv("PUBLIC_DATA_ONLY", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    from sqlalchemy import text

    from app.db import get_engine
    with get_engine().begin() as conn:  # ไฟล์ค้างจากเทสต์อื่น (raw_file ไม่ถูกล้างตอน seed)
        conn.execute(text("DELETE FROM raw_file_link"))
        conn.execute(text("DELETE FROM raw_file"))
    yield tmp_path
    from app.db import get_engine
    from sqlalchemy import text
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM law_upload"))


def _seed():
    from app.db import get_engine
    from app.seed import seed
    with get_engine().begin() as conn:
        return seed.run(conn)


def test_seed_skips_internal_and_register_internal_columns(public_env):
    from sqlalchemy import text

    from app.db import get_engine
    assert _seed()["register_rows"] == 4
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM legal_instrument WHERE eli_id = :e"), {"e": INTERNAL["id"]}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM legal_instrument WHERE classification = 'internal'")).scalar() == 0
        leaked = c.execute(text("""SELECT count(*) FROM law_register WHERE owner_unit IS NOT NULL OR note IS NOT NULL
                                   OR status_text IS NOT NULL OR cardinality(issues) > 0""")).scalar()
        assert leaked == 0
        assert c.execute(text("SELECT count(*) FROM raw_file WHERE classification = 'internal'")).scalar() == 0  # ไม่เก็บสำเนาทะเบียน
        # สถานะยังคำนวณได้ (ใช้หมายเหตุก่อนทิ้ง)
        assert c.execute(text("SELECT count(*) FROM law_register WHERE status = 'REPEALED'")).scalar() == 1


def test_register_json_roundtrip_without_xlsx(public_env, monkeypatch):
    from sqlalchemy import text

    from app.db import get_engine
    from app.seed.import_register import export_register_json
    _seed()
    out = public_env / "register-public.json"
    with get_engine().begin() as c:
        n = export_register_json(c, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert n == 4 and not any(k in data["rows"][0] for k in ("owner_unit", "note", "issues", "review_text"))
    monkeypatch.setenv("REGISTER_XLSX", str(public_env / "missing.xlsx"))
    assert _seed()["register_rows"] == 4
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM law_register WHERE status <> 'GROUP'")).scalar() == 3


def test_internal_raw_file_and_upload_refused(public_env, tmp_path):
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed.raw_files import add_file
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4 internal")
    with get_engine().begin() as conn, pytest.raises(ValueError, match="PUBLIC_DATA_ONLY"):
        add_file(conn, f, "internal")
    c = TestClient(app)
    r = c.post("/api/v1/laws/uploads", headers={"X-Demo-User": "lgl1"}, data={"doc_type": "ระเบียบ", "classification": "internal"},
               files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})
    assert r.status_code == 400 and "PUBLIC_DATA_ONLY" in r.json()["detail"]


def test_purge_removes_internal_files(public_env, tmp_path, monkeypatch):
    from sqlalchemy import text

    from app.db import get_engine
    from app.seed import public_only
    from app.seed.raw_files import add_file
    _seed()
    monkeypatch.delenv("PUBLIC_DATA_ONLY")      # ไฟล์ภายในที่ค้างจากก่อนเปิดโหมด
    f = tmp_path / "old.pdf"
    f.write_bytes(b"%PDF-1.4 old internal")
    with get_engine().begin() as conn:
        sha = add_file(conn, f, "internal")["sha256"]
    work = tmp_path / "work" / "ocr-compare" / "akson"
    work.mkdir(parents=True)
    (work / f"{sha}.txt").write_text("ข้อความภายใน", encoding="utf-8")
    monkeypatch.setenv("PUBLIC_DATA_ONLY", "true")
    monkeypatch.setenv("LEGAL_IMPORT_WORK", str(tmp_path / "work"))
    public_only.purge(apply=False)
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM raw_file WHERE sha256 = :s"), {"s": sha}).scalar() == 1
    public_only.purge(apply=True)
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM raw_file WHERE sha256 = :s"), {"s": sha}).scalar() == 0
    assert not (work / f"{sha}.txt").exists()
