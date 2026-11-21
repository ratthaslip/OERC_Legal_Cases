"""เพิ่ม/แก้ไขคดีจากหน้าเว็บ, กระบวนพิจารณา, เอกสารในสำนวน, ประวัติ และการนำเข้าทะเบียนคดีจาก Excel"""
import io
import os

import pytest

LGL1, LGL2, DIR, STF = ({"X-Demo-User": u} for u in ("lgl1", "lgl2", "dir1", "stf1"))
UNIQUE = "ทดสอบคำเฉพาะคำพิพากษาวิสุทธิกษัตริย์"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash",
                      RAW_STORE_DIR=str(tmp_path_factory.mktemp("raw")))
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
    return TestClient(app)


@pytest.fixture(autouse=True)
def sync(monkeypatch):
    from app.cases import manage
    monkeypatch.setattr(manage, "process_document_in_background", manage.process_document)


def case_body(black="คบ. 9001/2569", **kw):
    return {"black_no": black, "court_level": "ศาลปกครองชั้นต้น", "court_name": "ศาลปกครองกลาง",
            "subject": "คดีพิพาททดสอบเรื่องการเพิกถอนใบอนุญาตผลิตไฟฟ้า", "party_code": "EL-TEST-001",
            "energy_sector": "ไฟฟ้า", "incident_date": "2026-01-10", "filed_date": "2026-03-01", **kw}


def _detail(client, cid, h=LGL1):
    return client.get(f"/api/v1/court-cases/{cid}", headers=h)


def test_create_permissions_and_validation(client):
    assert client.post("/api/v1/court-cases", headers={"X-Demo-User": "pub"}, json=case_body()).status_code == 404
    assert client.post("/api/v1/court-cases", headers=STF, json=case_body()).status_code == 403
    assert client.post("/api/v1/court-cases", headers=LGL1, json=case_body(party_code="นาย ก")).status_code == 400
    assert client.post("/api/v1/court-cases", headers=LGL1, json=case_body(filed_date="2099-01-01")).status_code == 400
    assert client.post("/api/v1/court-cases", headers=LGL1, json=case_body(court_level="ศาลจังหวัด")).status_code == 400
    assert client.post("/api/v1/court-cases", headers=LGL1, json=case_body(owners=["lgl2"])).status_code == 403
    r = client.post("/api/v1/court-cases", headers=DIR, json=case_body("คบ. 9002/2569", classification="restricted"))
    assert r.status_code == 400   # คดีชั้นจำกัดต้องมีผู้รับผิดชอบ
    r = client.post("/api/v1/court-cases", headers=LGL1, json=case_body(origin_appeal_no="อธ. 1/2568"))
    assert r.status_code == 201, r.text
    cid = r.json()["case_id"]
    assert client.post("/api/v1/court-cases", headers=LGL2, json=case_body()).status_code == 409   # ซ้ำ
    d = _detail(client, cid).json()
    assert d["case"]["owners"] == ["lgl1"] and d["can_edit"] and not d["case"]["is_demo"]
    assert d["case"]["party_label"] == "ผู้ฟ้องคดี รหัส [EL-TEST-001]" and d["case"]["origin_appeal_no"] == "อธ. 1/2568"
    assert _detail(client, cid, LGL2).json()["can_edit"] is False
    found = client.get("/api/v1/court-cases", params={"q": "คบ. 9001/2569"}, headers=LGL2).json()["items"]
    assert found[0]["case_id"] == cid


def test_edit_conflict_and_history(client):
    cid = client.post("/api/v1/court-cases", headers=LGL1, json=case_body("คบ. 9010/2569")).json()["case_id"]
    k = _detail(client, cid).json()["case"]
    body = {**case_body("คบ. 9010/2569"), "row_version": k["row_version"], "red_no": "คด. 55/2569",
            "status": "FINAL", "result": "ยกฟ้อง"}
    assert client.put(f"/api/v1/court-cases/{cid}", headers=LGL2, json=body).status_code == 403   # ไม่ใช่ผู้รับผิดชอบ
    r = client.put(f"/api/v1/court-cases/{cid}", headers=LGL1, json=body)
    assert r.status_code == 200 and set(r.json()["changed"]) == {"red_no", "status", "result"}
    assert client.put(f"/api/v1/court-cases/{cid}", headers=LGL1, json=body).status_code == 409    # รุ่นเก่า
    same = {**body, "row_version": k["row_version"] + 1}
    assert client.put(f"/api/v1/court-cases/{cid}", headers=LGL1, json=same).json()["changed"] == []
    # ผอ.คบ. เปลี่ยนผู้รับผิดชอบได้ นิติกรเปลี่ยนเองไม่ได้
    assert client.put(f"/api/v1/court-cases/{cid}", headers=LGL1, json={**same, "owners": ["lgl2"]}).status_code == 403
    r = client.put(f"/api/v1/court-cases/{cid}", headers=DIR, json={**same, "owners": ["lgl2"]})
    assert r.json()["changed"] == ["owners"]
    assert _detail(client, cid, LGL2).json()["can_edit"] is True
    h = client.get(f"/api/v1/court-cases/{cid}/history", headers=LGL1).json()["items"]
    assert [(x["rev_no"], x["kind"], x["actor"]) for x in h] == [(3, "edit", "dir1"), (2, "edit", "lgl1"), (1, "create", "lgl1")]
    f = {c["field"]: c for c in h[1]["summary"]["fields"]}
    assert f["result"]["old"] is None and f["result"]["new"] == "ยกฟ้อง"
    import sqlalchemy.exc
    from sqlalchemy import text

    from app.db import get_engine
    with pytest.raises(sqlalchemy.exc.DBAPIError), get_engine().begin() as conn:
        conn.execute(text("DELETE FROM court_case_revision WHERE case_id = :i"), {"i": cid})


def test_proceedings(client):
    cid = client.post("/api/v1/court-cases", headers=LGL1, json=case_body("คบ. 9020/2569")).json()["case_id"]
    r = client.post(f"/api/v1/court-cases/{cid}/proceedings", headers=LGL1,
                    json={"proceeding_date": "2026-04-01", "title": "สำนักงานยื่นคำให้การ", "detail": "ชี้แจงข้อเท็จจริง"})
    pid = r.json()["proceeding_id"]
    client.post(f"/api/v1/court-cases/{cid}/proceedings", headers=LGL1,
                json={"proceeding_date": "2026-12-15", "title": "นัดนั่งพิจารณาคดีครั้งแรก"})   # วันนัดในอนาคตได้
    assert client.post(f"/api/v1/court-cases/{cid}/proceedings", headers=LGL2,
                       json={"proceeding_date": "2026-04-02", "title": "x x"}).status_code == 403
    assert [p["title"] for p in _detail(client, cid).json()["proceedings"]] == ["สำนักงานยื่นคำให้การ", "นัดนั่งพิจารณาคดีครั้งแรก"]
    assert client.delete(f"/api/v1/court-cases/{cid}/proceedings/{pid}", headers=LGL1).status_code == 200
    h = client.get(f"/api/v1/court-cases/{cid}/history", headers=LGL1).json()["items"]
    assert h[0]["kind"] == "proceeding" and h[0]["summary"]["proceedings_removed"] == ["2026-04-01 สำนักงานยื่นคำให้การ"]


def _docx(text_line: str) -> bytes:
    import docx
    d = docx.Document()
    for line in ["คำพิพากษา", "ศาลปกครองกลาง", text_line, "จึงพิพากษายกฟ้อง"]:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_documents(client):
    cid = client.post("/api/v1/court-cases", headers=LGL1,
                      json=case_body("คบ. 9030/2569", classification="restricted")).json()["case_id"]
    files = {"file": ("คำพิพากษา.docx", _docx(f"ข้อเท็จจริงฟังได้ว่า{UNIQUE}"), "application/octet-stream")}
    assert client.post(f"/api/v1/court-cases/{cid}/documents", headers=LGL1, data={"doc_type": "จดหมาย"},
                       files=files).status_code == 400
    bad = {"file": ("x.pdf", b"not a pdf", "application/pdf")}
    assert client.post(f"/api/v1/court-cases/{cid}/documents", headers=LGL1, data={"doc_type": "คำพิพากษา"},
                       files=bad).status_code == 400
    r = client.post(f"/api/v1/court-cases/{cid}/documents", headers=LGL1,
                    data={"doc_type": "คำพิพากษา", "doc_no": "คด. 55/2569", "doc_date": "2026-06-01"}, files=files)
    assert r.status_code == 201, r.text
    did = r.json()["document_id"]
    doc = next(x for x in _detail(client, cid).json()["documents"] if x["document_id"] == did)
    assert doc["status"] == "ready" and doc["method"] == "docx" and UNIQUE in doc["excerpt"] and doc["has_file"]
    assert "sha256" not in doc
    hits = client.get("/api/v1/court-cases", params={"q": UNIQUE}, headers=LGL1).json()["items"]
    assert hits and hits[0]["case_id"] == cid
    # คดีชั้นจำกัด: ผู้ไม่ใช่ผู้รับผิดชอบไม่เห็นทั้งคดีและไฟล์
    assert not any(k["case_id"] == cid for k in client.get("/api/v1/court-cases", params={"q": UNIQUE}, headers=LGL2).json()["items"])
    assert client.get(f"/api/v1/court-cases/{cid}/documents/{did}/file", headers=LGL2).status_code == 404
    f = client.get(f"/api/v1/court-cases/{cid}/documents/{did}/file", headers=LGL1)
    assert f.status_code == 200 and f.content[:2] == b"PK"
    assert client.delete(f"/api/v1/court-cases/{cid}/documents/{did}", headers=LGL1).status_code == 200
    from sqlalchemy import text

    from app.db import get_engine
    with get_engine().connect() as conn:   # ข้อความของเอกสารที่เอาออกไม่อยู่ในดัชนีค้นหาแล้ว
        assert not conn.execute(text("SELECT 1 FROM search_chunk WHERE chunk_text LIKE :q"), {"q": f"%{UNIQUE}%"}).first()


def _workbook(client, case_rows, proc_rows=()):
    from openpyxl import load_workbook
    t = client.get("/api/v1/court-cases/imports/template", headers=LGL1)
    assert t.status_code == 200
    wb = load_workbook(io.BytesIO(t.content))
    heads = [c.value.rstrip("*") for c in wb["คดี"][1]]
    for r in case_rows:
        wb["คดี"].append([r.get(h) for h in heads])
    pheads = [c.value.rstrip("*") for c in wb["กระบวนพิจารณา"][1]]
    for r in proc_rows:
        wb["กระบวนพิจารณา"].append([r.get(h) for h in pheads])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, data, h=LGL1):
    return client.post("/api/v1/court-cases/imports", headers=h,
                       files={"file": ("ทะเบียนคดี.xlsx", data, "application/octet-stream")})


ROW = {"เลขคดีดำ": "คบ. 9100/2569", "ศาล": "ศาลปกครองกลาง", "ชั้นศาล": "ศาลปกครองชั้นต้น",
       "เรื่อง": "คดีพิพาทนำเข้าจาก Excel เรื่องค่าไฟฟ้าผันแปร", "รหัสคู่กรณี": "EL-IMP-01", "ประเภทกิจการพลังงาน": "ไฟฟ้า",
       "วันเกิดเหตุ": "15/02/2569", "สถานะ": "อยู่ระหว่างพิจารณา"}


def test_excel_import_review_and_apply(client):
    from datetime import datetime
    existing = client.post("/api/v1/court-cases", headers=LGL1, json=case_body("คบ. 9101/2569")).json()["case_id"]
    rows = [ROW,
            {**ROW, "เลขคดีดำ": "คบ. 9101/2569", "เลขคดีแดง": "คด. 77/2569", "สถานะ": "ถึงที่สุด", "ผลคดี": "ยกฟ้อง",
             "เรื่อง": case_body()["subject"], "รหัสคู่กรณี": "EL-TEST-001", "วันเกิดเหตุ": datetime(2569, 1, 10),
             "วันยื่นฟ้อง": "1 มี.ค. 2569"},
            {**ROW, "เลขคดีดำ": "คบ. 9102/2569", "ชั้นศาล": "ศาลจังหวัด", "รหัสคู่กรณี": "นาย ข"}]
    procs = [{"เลขคดีดำ": "คบ. 9100/2569", "ศาล": "ศาลปกครองกลาง", "วันที่": "20/03/2569", "รายการ": "ศาลรับคำฟ้อง"},
             {"เลขคดีดำ": "คบ. 9999/2569", "ศาล": "ศาลปกครองกลาง", "วันที่": "20/03/2569", "รายการ": "ไม่มีคดีนี้"}]
    v = _upload(client, _workbook(client, rows, procs)).json()
    assert v["error_count"] == 2 and v["counts"] == {"create": 2, "update": 1, "unchanged": 0, "proceedings": 2}
    bad = next(c for c in v["cases"] if c["row"] == 4)
    assert any("ชั้นศาล" in e for e in bad["errors"]) and any("รหัสคู่กรณี" in e for e in bad["errors"])
    assert client.post(f"/api/v1/court-cases/imports/{v['import_id']}/submit", headers=LGL1).status_code == 400
    assert client.get("/api/v1/court-cases/imports", headers=STF).status_code == 403

    v = _upload(client, _workbook(client, rows[:2], procs[:1])).json()
    assert v["error_count"] == 0, v["cases"]
    iid = v["import_id"]
    assert client.post(f"/api/v1/court-cases/imports/{iid}/approve", headers=LGL2, json={}).status_code == 409   # ยังไม่ส่ง
    client.post(f"/api/v1/court-cases/imports/{iid}/submit", headers=LGL1)
    assert client.post(f"/api/v1/court-cases/imports/{iid}/approve", headers=LGL1, json={}).status_code == 403
    assert client.get(f"/api/v1/court-cases/imports/{iid}", headers=LGL2).json()["can_approve"] is True
    a = client.post(f"/api/v1/court-cases/imports/{iid}/approve", headers=LGL2, json={"note": "ตรวจแล้ว"})
    assert a.status_code == 200 and a.json()["status"] == "applied", a.text
    res = a.json()["result"]
    assert len(res["created"]) == 1 and res["updated"] == [existing] and res["proceedings_added"] == 1
    new = _detail(client, res["created"][0]).json()
    assert new["case"]["owners"] == ["lgl1"] and new["case"]["incident_date"] == "2026-02-15"
    assert [p["title"] for p in new["proceedings"]] == ["ศาลรับคำฟ้อง"]
    upd = _detail(client, existing).json()["case"]
    assert upd["red_no"] == "คด. 77/2569" and upd["status"] == "FINAL" and upd["filed_date"] == "2026-03-01"
    h = client.get(f"/api/v1/court-cases/{existing}/history", headers=LGL1).json()["items"]
    assert h[0]["kind"] == "import" and h[0]["actor"] == "lgl2"

    again = _upload(client, _workbook(client, rows[:2], procs[:1])).json()   # นำเข้าซ้ำ → ไม่เปลี่ยน
    assert again["counts"]["unchanged"] == 2 and again["counts"]["create"] == 0
    # ผู้ไม่ใช่ผู้รับผิดชอบปรับปรุงคดีเดิมผ่าน Excel ไม่ได้
    other = _upload(client, _workbook(client, [{**rows[1], "ผลคดี": "เพิกถอนคำสั่ง"}]), h=LGL2).json()
    assert other["error_count"] == 1 and "ผู้รับผิดชอบ" in other["cases"][0]["errors"][0]


def test_public_data_only_machine(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_DATA_ONLY", "true")
    r = client.post("/api/v1/court-cases", headers=LGL1, json=case_body("คบ. 9200/2569"))
    cid = r.json()["case_id"]
    assert _detail(client, cid).json()["case"]["is_demo"] is True   # ติดป้ายข้อมูลสาธิต
    files = {"file": ("คำพิพากษา.docx", _docx("ข้อความ"), "application/octet-stream")}
    r = client.post(f"/api/v1/court-cases/{cid}/documents", headers=LGL1, data={"doc_type": "คำพิพากษา"}, files=files)
    assert r.status_code == 400 and r.json()["code"] == "PUBLIC_DATA_ONLY"
    r = _upload(client, _workbook(client, [{**ROW, "เลขคดีดำ": "คบ. 9201/2569"}]))
    assert r.status_code == 400 and r.json()["code"] == "PUBLIC_DATA_ONLY"
