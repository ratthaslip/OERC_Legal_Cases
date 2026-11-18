"""ตราสารภายใน (ระเบียบบริหารงานของสำนักงาน): เห็นเฉพาะเจ้าหน้าที่ — สารบัญ, หน้ามาตรา, ค้นหา, ผู้ช่วย AI, ไฟล์ต้นฉบับ"""
import json
import os
from pathlib import Path

import pytest

LAWS = Path(__file__).resolve().parents[1] / "app" / "seed" / "laws"


def _internal_instrument() -> dict:
    for f in sorted(LAWS.rglob("*.json")):
        for inst in json.loads(f.read_text(encoding="utf-8")):
            if inst.get("classification") == "internal":
                return inst
    pytest.skip("ไม่มีตราสารภายในในชุดข้อมูล")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash",
                      RAW_STORE_DIR=str(tmp_path_factory.mktemp("raw")))
    os.environ.pop("REGISTER_XLSX", None)
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
    return TestClient(app)


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
