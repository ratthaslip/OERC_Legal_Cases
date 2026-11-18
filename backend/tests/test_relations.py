"""ตาราง law_relation: สกัดการอ้างถึง, สร้างใหม่ซ้ำได้ผลเท่าเดิม, เดินหลายทอดเฉพาะเส้นที่อนุมัติ"""
import json
import os

import pytest

from app.law.relations import extract_refs


def test_extract_refs_same_instrument():
    body = "มาตรา 56 ในกรณีที่ผู้รับใบอนุญาตไม่ปฏิบัติตามระเบียบที่คณะกรรมการประกาศกำหนดตามมาตรา 50 และมาตรา 51"
    assert extract_refs(body, "มาตรา 56") == [("มาตรา", "50", False), ("มาตรา", "51", False)]


def test_extract_refs_skips_self_and_other_laws():
    body = "มาตรา 44 ภายใต้บังคับมาตรา 48 ให้นำมาตรา 5 แห่งพระราชบัญญัติจัดตั้งศาลปกครอง มาใช้ ตามมาตรา 44 วรรคหนึ่ง"
    assert extract_refs(body, "มาตรา 44") == [("มาตรา", "48", False)]


def test_extract_refs_thai_digits():
    assert extract_refs("ข้อ ๙ ภายใต้บังคับข้อ ๘", "ข้อ 9") == [("ข้อ", "8", False)]


def test_extract_refs_erc_regulation_points_to_parent_act():
    body = "ข้อ 5 ต้องปฏิบัติตามกฎหมายที่กำหนดไว้ในมาตรา 48 และตามข้อ 4"
    assert extract_refs(body, "ข้อ 5", erc_subordinate=True) == [("มาตรา", "48", True), ("ข้อ", "4", False)]
    named = "ข้อ 2 ตามมาตรา 121 แห่งพระราชบัญญัติการประกอบกิจการพลังงาน พ.ศ. 2550"
    assert extract_refs(named, "ข้อ 2") == [("มาตรา", "121", True)]


@pytest.fixture(scope="module")
def conn_client():
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash")
    os.environ.pop("REGISTER_XLSX", None)
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
    return get_engine(), TestClient(app)


def test_rebuild_is_idempotent(conn_client):
    from app.law import relations
    engine, _ = conn_client
    with engine.begin() as conn:
        first = relations.rebuild(conn)
        second = relations.rebuild(conn)
        assert first == second
        assert first["refers_to"] > 100 and first["law_json"] > 100


def test_amendment_edge_resolves_to_provision(conn_client):
    from app.law import relations
    engine, _ = conn_client
    with engine.begin() as conn:
        inc = relations.edges_of(conn, "eli/th/reg/erc/2553/complaint/clause-21")["incoming"]
    amends = [e for e in inc if e["rel_type"] == "amends"]
    assert len(amends) == 1  # สองรุ่นบันทึกเส้นเดียวกัน → แสดงครั้งเดียว
    assert amends[0]["src_eli"] == "eli/th/reg/erc/2558/complaint-amend-2/clause-4"


def test_provision_api_returns_relations(conn_client):
    _, c = conn_client
    r = c.get("/api/v1/laws/provisions/eli/th/act/2550/energy-industry/sec-56", headers={"X-Demo-User": "pub"})
    out = r.json()["relations"]["outgoing"]
    assert any(e["dst_eli"].endswith("/sec-50") and e["rel_type"] == "refers_to" for e in out)


def test_walk_uses_only_approved_manual_edges(conn_client, tmp_path, monkeypatch):
    from app.law import relations
    engine, c = conn_client
    a, b, d = (f"eli/th/act/2550/energy-industry/sec-{n}" for n in (56, 121, 0))
    (tmp_path / "remedy.json").write_text(json.dumps({"status": "approved", "approved_by": "นิติกร ทดสอบ", "edges": [
        {"id": "T1", "type": "remedy", "from": a, "to": b, "note": "อุทธรณ์ต่อ กกพ."},
        {"id": "T2", "type": "remedy", "from": b, "to": "eli/th/act/2542/admin-court/sec-49", "note": "ฟ้องศาลปกครอง"},
        {"id": "T3", "type": "remedy", "from": "eli/th/act/2542/admin-court/sec-49", "to": a, "note": "วนกลับ"}]}),
        encoding="utf-8")
    (tmp_path / "x.draft.json").write_text(json.dumps({"status": "approved", "edges": [
        {"type": "remedy", "from": a, "to": d}]}), encoding="utf-8")
    monkeypatch.setattr(relations, "RELATIONS_DIR", tmp_path)
    with engine.begin() as conn:
        assert relations.rebuild(conn)["manual"] == 3
        steps = relations.walk(conn, a)
    assert [s["dst_eli"] for s in steps] == [b, "eli/th/act/2542/admin-court/sec-49"]  # ไม่วนกลับ ไม่โหลดไฟล์ draft
    r = c.get("/api/v1/laws/relations/walk", params={"eli": a})
    assert len(r.json()["items"]) == 2
    with engine.begin() as conn:
        monkeypatch.undo()
        relations.rebuild(conn)
