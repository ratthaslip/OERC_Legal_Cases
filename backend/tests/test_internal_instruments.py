"""ตราสารภายใน (ระเบียบบริหารงานของสำนักงาน): เห็นเฉพาะเจ้าหน้าที่ — สารบัญ, หน้ามาตรา, ค้นหา, ผู้ช่วย AI, ไฟล์ต้นฉบับ"""
import json
import os
from pathlib import Path

import pytest

# ตราสารภายในจริงไม่อยู่ใน repo (วางที่ LAWS_INTERNAL_DIR บนเครื่องของสำนักงาน) — เทสต์ใช้ตราสารสมมติ
INTERNAL = {
    "id": "eli/th/reg/erc/2599/test-internal", "type": "ระเบียบ", "code": "ระเบียบทดสอบภายใน",
    "title": "ระเบียบสำนักงานคณะกรรมการกำกับกิจการพลังงาน ว่าด้วยสวัสดิการทดสอบภายในของพนักงาน พ.ศ. 2599",
    "issuer": "สำนักงาน กกพ.", "category": "การบริหารองค์กร", "classification": "internal",
    "publishedDate": "2026-01-01", "effectiveDate": "2026-01-02", "isDemo": False, "source": "ทดสอบ",
    "registerTitle": None, "preamble": None, "signed": None, "importNotes": [],
    "provisions": [{"id": "eli/th/reg/erc/2599/test-internal/clause-1", "number": "ข้อ 1", "part": None, "sourcePage": None,
                    "versions": [{"version": 1, "validFrom": "2026-01-02", "validTo": None, "summary": None,
                                  "relations": [], "amendingInstrument": "ตัวบทตั้งต้น",
                                  "text": "พนักงานมีสิทธิได้รับสวัสดิการทดสอบภายในปีละหนึ่งครั้งตามระเบียบสวัสดิการทดสอบภายในนี้"}]}],
}


def _internal_instrument() -> dict:
    return INTERNAL


def write_internal_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "test-internal.json").write_text(json.dumps([INTERNAL], ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash",
                      RAW_STORE_DIR=str(tmp_path_factory.mktemp("raw")),
                      LAWS_INTERNAL_DIR=str(write_internal_dir(tmp_path_factory.mktemp("laws-internal"))))
    os.environ.pop("REGISTER_XLSX", None)
    os.environ.pop("PUBLIC_DATA_ONLY", None)
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
    yield TestClient(app)
    os.environ.pop("LAWS_INTERNAL_DIR", None)


def _h(user: str) -> dict:
    return {"X-Demo-User": user}


def test_instrument_list_and_provision(client):
    inst = _internal_instrument()
    pub = {i["eli_id"] for i in client.get("/api/v1/laws/instruments", headers=_h("pub")).json()["items"]}
    stf = {i["eli_id"] for i in client.get("/api/v1/laws/instruments", headers=_h("lgl1")).json()["items"]}
    assert inst["id"] not in pub and inst["id"] in stf
    eli = inst["provisions"][0]["id"]
    assert client.get(f"/api/v1/laws/provisions/{eli}", headers=_h("pub")).status_code == 404  # ตอบเหมือนไม่มี
    d = client.get(f"/api/v1/laws/provisions/{eli}", headers=_h("lgl1")).json()
    assert d["provision"]["classification"] == "internal"
    cats_pub = client.get("/api/v1/laws/instruments", headers=_h("pub")).json()["categories"]
    assert "การบริหารองค์กร" not in cats_pub


def test_search_excludes_internal_for_public(client):
    inst = _internal_instrument()
    q = inst["title"][:60]
    pub = client.get("/api/v1/laws/search", params={"q": q}, headers=_h("pub")).json()["items"]
    stf = client.get("/api/v1/laws/search", params={"q": q}, headers=_h("lgl1")).json()["items"]
    prefix = inst["id"] + "/"
    assert not any((h.get("eli_id") or "").startswith(prefix) for h in pub)
    assert any((h.get("eli_id") or "").startswith(prefix) for h in stf)


def _sources(client, q: str, user: str) -> list[str]:
    r = client.post("/api/v1/assistant/answers", json={"question": q}, headers=_h(user))
    assert r.status_code == 200
    out = []
    for b in r.text.strip().split("\n\n"):
        if b.startswith("event: sources"):
            out += [s.get("eli_id") or "" for s in json.loads(b.split("\n", 1)[1][6:])["sources"]]
    return out


def test_assistant_never_cites_internal_for_public(client):
    inst = _internal_instrument()
    q = inst["title"][:80]
    assert not any(e.startswith(inst["id"] + "/") for e in _sources(client, q, "pub"))
    assert any(e.startswith(inst["id"] + "/") for e in _sources(client, q, "lgl1"))  # เจ้าหน้าที่ยังได้


def test_raw_file_of_internal_instrument_is_internal(client, tmp_path):
    from sqlalchemy import text

    from app.db import get_engine
    from app.seed.raw_files import add_file
    inst = _internal_instrument()
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF internal regulation")
    with get_engine().begin() as conn:
        res = add_file(conn, f, "public")  # เก็บเป็น public มาก่อน (เช่น จาก zip) — ต้องยังถือเป็นภายใน
        conn.execute(text("INSERT INTO raw_file_link (sha256, instrument_eli, role) VALUES (:s, :e, 'text') "
                          "ON CONFLICT DO NOTHING"), {"s": res["sha256"], "e": inst["id"]})
    assert client.get(f"/api/v1/raw/{res['sha256']}", headers=_h("pub")).status_code == 404
    assert client.get(f"/api/v1/raw/{res['sha256']}", headers=_h("lgl1")).status_code == 200
