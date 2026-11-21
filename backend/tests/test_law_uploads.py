"""นำเข้าเอกสารกฎหมายจากหน้าเว็บ: LGL/ADM เท่านั้น, ร่าง → แก้ → อนุมัติโดยคนอื่น (หลักสี่ตา) → ค้นเจอ, seed แล้วไม่หาย"""
import io
import os
import shutil

import pytest

ADM = {"X-Demo-User": "adm1"}
LGL1 = {"X-Demo-User": "lgl1"}
LGL2 = {"X-Demo-User": "lgl2"}
TITLE = "ประกาศคณะกรรมการกำกับกิจการพลังงาน เรื่อง การทดสอบนำเข้าเอกสารผ่านหน้าเว็บ พ.ศ. 2569"
UNIQUE = "ทดสอบคำเฉพาะวิริยะอุตสาหะ"


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
    from sqlalchemy import text

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
        conn.execute(text("DELETE FROM law_upload"))
    yield TestClient(app)
    with get_engine().begin() as conn:  # ไม่ให้ฉบับที่อนุมัติในเทสต์นี้ติดไปกับ seed ของเทสต์อื่น
        conn.execute(text("DELETE FROM law_upload"))


@pytest.fixture(autouse=True)
def sync(monkeypatch):
    from app.law import uploads
    monkeypatch.setattr(uploads, "process_in_background", uploads.process)
    monkeypatch.setattr(uploads, "_after_publish_embedding", lambda: None)


def docx_bytes() -> bytes:
    import docx
    d = docx.Document()
    for line in [TITLE, "อาศัยอำนาจตามความในมาตรา 11 แห่งพระราชบัญญัติการประกอบกิจการพลังงาน พ.ศ. 2550 จึงออกประกาศไว้ดังต่อไปนี้",
                 "ข้อ 1 ประกาศนี้เรียกว่า “ประกาศคณะกรรมการกำกับกิจการพลังงาน เรื่อง การทดสอบนำเข้าเอกสารผ่านหน้าเว็บ พ.ศ. 2569”",
                 "ข้อ 2 ประกาศนี้ให้ใช้บังคับตั้งแต่วันถัดจากวันประกาศในราชกิจจานุเบกษาเป็นต้นไป",
                 f"ข้อ 3 ผู้รับใบอนุญาตต้องรายงานข้อมูลการ{UNIQUE}ต่อสำนักงานภายในสามสิบวัน",
                 "ประกาศ ณ วันที่ 1 มีนาคม พ.ศ. 2569", "ประธานกรรมการกำกับกิจการพลังงาน"]:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _upload(client, headers=LGL1, **form):
    data = {"doc_type": "ประกาศ", "classification": "public", **form}
    return client.post("/api/v1/laws/uploads", headers=headers, data=data,
                       files={"file": ("ประกาศทดสอบ.docx", docx_bytes(),
                                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})


def _submit(client, uid, headers=LGL1):
    r = client.post(f"/api/v1/laws/uploads/{uid}/submit", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "submitted", r.text
    return r


def test_only_lgl_and_adm(client):
    for u in ("pub", "stf1", "dir1"):
        h = {"X-Demo-User": u}
        assert client.get("/api/v1/laws/uploads", headers=h).status_code == 404
        assert _upload(client, headers=h).status_code == 404
    assert client.get("/api/v1/laws/uploads", headers=LGL1).status_code == 200


def test_rejects_bad_files(client):
    bad = client.post("/api/v1/laws/uploads", headers=LGL1, data={"doc_type": "ประกาศ"},
                      files={"file": ("x.exe", b"MZ..", "application/octet-stream")})
    assert bad.status_code == 400
    fake = client.post("/api/v1/laws/uploads", headers=LGL1, data={"doc_type": "ประกาศ"},
                       files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert fake.status_code == 400
    assert _upload(client, doc_type="จดหมาย").status_code == 400
    # การปฏิเสธไฟล์ถูกบันทึกใน audit log แม้คำขอไม่สำเร็จ
    from sqlalchemy import text

    from app.db import get_engine
    with get_engine().connect() as c:
        r = c.execute(text("""SELECT outcome, detail FROM audit_event WHERE action = 'law.upload.reject_file'
                              ORDER BY seq DESC LIMIT 1""")).mappings().first()
    assert r["outcome"] == "DENY" and "PDF" in r["detail"]["reason"]


def test_virus_found_is_rejected(client, monkeypatch):
    from app import filecheck
    monkeypatch.setenv("AV_SCAN", "required")
    monkeypatch.setattr(filecheck, "clamd_scan", lambda data: "Eicar-Test-Signature")
    r = _upload(client)
    assert r.status_code == 422 and "มัลแวร์" in r.json()["detail"]
    monkeypatch.setattr(filecheck, "clamd_scan", lambda data: None)
    assert _upload(client).status_code == 200


def test_upload_edit_approve_search_and_survive_seed(client):
    r = _upload(client, effective_date="2026-03-02")
    assert r.status_code == 200
    uid = r.json()["upload_id"]
    v = client.get(f"/api/v1/laws/uploads/{uid}", headers=LGL1).json()
    assert v["status"] == "draft" and v["method"] == "docx" and v["provision_count"] == 3
    d = v["draft"]
    assert d["title"] == TITLE and d["effectiveDate"] == "2026-03-02" and d["id"].endswith(f"/u{uid}")
    assert [p["number"] for p in d["provisions"]] == ["ข้อ 1", "ข้อ 2", "ข้อ 3"]

    # ยังไม่อนุมัติ → ค้นไม่เจอ
    q = {"q": UNIQUE}
    found = lambda h: any((i.get("eli_id") or "").startswith(d["id"]) for i in  # noqa: E731
                          client.get("/api/v1/laws/search", params=q, headers=h).json()["items"])
    assert not found(LGL1)

    # แก้ร่าง: ชื่อย่อ + ข้อความข้อ 3
    provs = [{"number": p["number"], "part": p["part"], "text": p["versions"][0]["text"]} for p in d["provisions"]]
    provs[2]["text"] += " (แก้ไขโดยผู้ตรวจทาน)"
    e = client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL1, json={"code": "ประกาศทดสอบเว็บ", "provisions": provs})
    assert e.status_code == 200 and e.json()["draft"]["code"] == "ประกาศทดสอบเว็บ"

    # ยังไม่ส่งตรวจ → อนุมัติไม่ได้; ส่งตรวจแล้วแก้ไม่ได้
    assert client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=LGL2, json={}).status_code == 409
    _submit(client, uid)
    assert client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL1, json={"code": "y"}).status_code == 409
    # หลักสี่ตา: ผู้นำเข้าอนุมัติเองไม่ได้
    assert client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=LGL1, json={}).status_code == 403
    a = client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=LGL2, json={"note": "ตรวจแล้ว"})
    assert a.status_code == 200 and a.json()["status"] == "approved" and a.json()["reviewed_by"] == "lgl2"
    eli = a.json()["eli_id"]
    assert client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL1, json={"code": "x"}).status_code == 409

    items = client.get("/api/v1/laws/search", params=q, headers={"X-Demo-User": "pub"}).json()["items"]
    assert items and items[0]["eli_id"].startswith(eli)  # คำเฉพาะ → อันดับแรก

    # seed ใหม่ → ยังอยู่ (นำเข้าซ้ำจากร่างที่อนุมัติ)
    from sqlalchemy import text

    from app.db import get_engine
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
        n = conn.execute(text("SELECT count(*) FROM provision p JOIN legal_instrument i USING (instrument_id) "
                              "WHERE i.eli_id = :e"), {"e": eli}).scalar()
        body = conn.execute(text("""SELECT body_text FROM provision_version pv JOIN provision p USING (provision_id)
                                    WHERE p.eli_id = :p"""), {"p": f"{eli}/clause-3"}).scalar()
        notes = conn.execute(text("SELECT count(*) FROM law_open_issue WHERE instrument_eli = :e"), {"e": eli}).scalar()
    assert n == 3 and body.endswith("(แก้ไขโดยผู้ตรวจทาน)") and notes >= 1
    assert found(LGL1)


def test_internal_upload_hidden_from_public(client):
    uid = _upload(client, classification="internal", effective_date="2026-03-02").json()["upload_id"]
    _submit(client, uid)
    client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=ADM, json={})
    q = {"q": UNIQUE}
    pub = client.get("/api/v1/laws/search", params=q, headers={"X-Demo-User": "pub"}).json()["items"]
    stf = client.get("/api/v1/laws/search", params=q, headers=LGL1).json()["items"]
    eli = client.get(f"/api/v1/laws/uploads/{uid}", headers=ADM).json()["eli_id"]
    assert not any(i["eli_id"].startswith(eli) for i in pub) and any(i["eli_id"].startswith(eli) for i in stf)


def test_reject_and_delete(client):
    uid = _upload(client, effective_date="2026-03-02").json()["upload_id"]
    assert client.post(f"/api/v1/laws/uploads/{uid}/reject", headers=LGL2, json={"note": "ไฟล์ผิดฉบับ"}).json()["status"] == "rejected"
    assert client.delete(f"/api/v1/laws/uploads/{uid}", headers=LGL2).status_code == 403   # ไม่ใช่ผู้นำเข้า
    assert client.delete(f"/api/v1/laws/uploads/{uid}", headers=LGL1).status_code == 200


def test_register_link(client):
    from sqlalchemy import text

    from app.db import get_engine
    with get_engine().begin() as conn:
        rid = conn.execute(text("""INSERT INTO law_register (sheet_row, reg_no, group_no, doc_type, status, title_th, effective_date)
                                   VALUES (99999, '999.1', '999', 'ประกาศ', 'IN_FORCE', :t, '2026-04-01') RETURNING register_id"""),
                           {"t": TITLE + " (ทะเบียน)"}).scalar()
    opts = client.get("/api/v1/laws/uploads/register-options", params={"q": "การทดสอบนำเข้า"}, headers=LGL1).json()["items"]
    assert any(o["register_id"] == rid for o in opts)
    uid = _upload(client, register_id=str(rid)).json()["upload_id"]
    d = client.get(f"/api/v1/laws/uploads/{uid}", headers=LGL1).json()["draft"]
    assert d["effectiveDate"] == "2026-04-01" and d["registerTitle"].endswith("(ทะเบียน)")
    _submit(client, uid)
    eli = client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=ADM, json={}).json()["eli_id"]
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT instrument_eli FROM law_register WHERE register_id = :r"), {"r": rid}).scalar() == eli


@pytest.mark.skipif(not shutil.which("tesseract"), reason="ไม่มี tesseract")
def test_scanned_pdf_goes_through_ocr(client, tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    fonts = [p for p in ("/usr/share/fonts/truetype/tlwg/Garuda.ttf", "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
                         "/usr/share/fonts/opentype/tlwg/Garuda.otf", "/usr/share/fonts/opentype/tlwg/Loma.otf") if os.path.exists(p)]
    if not fonts:
        pytest.skip("ไม่มีฟอนต์ไทยสำหรับสร้างภาพทดสอบ")
    img = Image.new("L", (2480, 3508), 255)
    dr = ImageDraw.Draw(img)
    f = ImageFont.truetype(fonts[0], 56)
    y = 250
    for line in [TITLE[:60], TITLE[60:], "ข้อ ๑ ประกาศนี้ให้ใช้บังคับตั้งแต่วันถัดจากวันประกาศ",
                 "ข้อ ๒ ผู้รับใบอนุญาตต้องรายงานข้อมูลต่อสำนักงานภายในสามสิบวัน"]:
        dr.text((250, y), line, font=f, fill=0)
        y += 120
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=300)
    r = client.post("/api/v1/laws/uploads", headers=LGL1, data={"doc_type": "ประกาศ", "effective_date": "2026-03-02"},
                    files={"file": ("scan.pdf", buf.getvalue(), "application/pdf")})
    v = client.get(f"/api/v1/laws/uploads/{r.json()['upload_id']}", headers=LGL1).json()
    assert v["status"] == "draft", v.get("error")
    assert v["method"] == "ocr" and any("OCR" in n for n in v["notes"])


def test_missing_date_makes_draft_but_blocks_approval(client):
    uid = _upload(client).json()["upload_id"]   # เอกสารทดสอบไม่มีหัวราชกิจจาฯ แต่มีวันที่ลงนาม
    d = client.get(f"/api/v1/laws/uploads/{uid}", headers=LGL1).json()
    assert d["status"] == "draft" and d["draft"]["effectiveDate"] == "2026-03-01"   # ใช้วันที่ลงนาม


def test_no_date_at_all(client, monkeypatch):
    from app.law import docparse, uploads
    real = docparse.build_instrument

    def no_sign(paras, **kw):
        return real([p for p in paras if not p.startswith("ประกาศ ณ")], **kw)
    monkeypatch.setattr(uploads.docparse, "build_instrument", no_sign)
    uid = _upload(client).json()["upload_id"]
    v = client.get(f"/api/v1/laws/uploads/{uid}", headers=LGL1).json()
    assert v["status"] == "draft" and v["draft"]["effectiveDate"] is None and "ระบุก่อนอนุมัติ" in v["notes"][0]
    assert client.post(f"/api/v1/laws/uploads/{uid}/submit", headers=LGL1).status_code == 400
    v = client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL1, json={"effectiveDate": "2026-05-01"}).json()
    assert v["draft"]["provisions"][0]["versions"][0]["validFrom"] == "2026-05-01"
    _submit(client, uid)
    assert client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=LGL2, json={}).status_code == 200


def _provs(d):
    return [{"number": p["number"], "part": p["part"], "text": p["versions"][0]["text"]} for p in d["provisions"]]


def test_revisions_and_diff_show_what_changed(client):
    uid = _upload(client, effective_date="2026-03-02").json()["upload_id"]
    d = client.get(f"/api/v1/laws/uploads/{uid}", headers=LGL1).json()["draft"]
    provs = _provs(d)
    provs[2]["text"] = provs[2]["text"].replace("สามสิบวัน", "สิบห้าวัน")
    provs.append({"number": "ข้อ 4", "part": None, "text": "ให้ประธานกรรมการรักษาการตามประกาศนี้"})
    client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL1, json={"provisions": provs})
    # บันทึกโดยไม่เปลี่ยนอะไร → ไม่สร้างรุ่นใหม่
    client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL1, json={"provisions": provs})
    client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL2, json={"code": "ชื่อย่อที่ lgl2 แก้"})

    revs = client.get(f"/api/v1/laws/uploads/{uid}/revisions", headers=LGL1).json()["items"]
    assert [(r["rev_no"], r["kind"], r["actor"]) for r in revs] == [(1, "extract", "system"), (2, "edit", "lgl1"),
                                                                    (3, "edit", "lgl2")]
    assert revs[1]["summary"]["changed"] == ["ข้อ 3"] and revs[1]["summary"]["added"] == ["ข้อ 4"]

    df = client.get(f"/api/v1/laws/uploads/{uid}/diff", headers=LGL1).json()   # ผลถอดข้อความ → ร่างล่าสุด
    assert df["base"]["rev_no"] == 1 and df["head"]["rev_no"] == 3
    assert [f["field"] for f in df["fields"]] == ["code"]
    ch = {p["key"]: p for p in df["provisions"]}
    assert set(ch) == {"ข้อ 3", "ข้อ 4"} and ch["ข้อ 4"]["change"] == "added"
    segs = ch["ข้อ 3"]["segments"]
    assert "".join(x["text"] for x in segs if x["op"] != "ins") == d["provisions"][2]["versions"][0]["text"]
    assert any(x["op"] == "del" and "สาม" in x["text"] for x in segs) and any(x["op"] == "ins" and "ห้า" in x["text"] for x in segs)
    one = client.get(f"/api/v1/laws/uploads/{uid}/diff", params={"base": 2, "head": 3}, headers=LGL1).json()
    assert one["provisions"] == [] and one["summary"]["fields"] == ["code"]

    # ประวัติแก้/ลบไม่ได้
    import sqlalchemy.exc
    from sqlalchemy import text

    from app.db import get_engine
    with pytest.raises(sqlalchemy.exc.DBAPIError), get_engine().begin() as conn:
        conn.execute(text("UPDATE law_upload_revision SET actor = 'x' WHERE upload_id = :i"), {"i": uid})
    with pytest.raises(sqlalchemy.exc.DBAPIError), get_engine().begin() as conn:
        conn.execute(text("DELETE FROM law_upload_revision WHERE upload_id = :i"), {"i": uid})


def test_editor_and_submitter_cannot_approve(client):
    uid = _upload(client, effective_date="2026-03-02").json()["upload_id"]
    client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL2, json={"code": "แก้โดย lgl2"})
    _submit(client, uid)
    v = client.get(f"/api/v1/laws/uploads/{uid}", headers=LGL2).json()
    assert v["editors"] == ["lgl2"] and v["can_approve"] is False
    r = client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=LGL2, json={})
    assert r.status_code == 403 and "ผู้แก้ร่าง" in r.json()["detail"]
    assert client.get(f"/api/v1/laws/uploads/{uid}", headers=ADM).json()["can_approve"] is True
    assert client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=ADM, json={}).status_code == 200


def test_return_withdraw_and_reprocess_keep_history(client):
    uid = _upload(client, effective_date="2026-03-02").json()["upload_id"]
    client.put(f"/api/v1/laws/uploads/{uid}", headers=LGL1, json={"code": "รุ่นที่แก้"})
    _submit(client, uid)
    assert client.post(f"/api/v1/laws/uploads/{uid}/withdraw", headers=LGL2).status_code == 403
    assert client.post(f"/api/v1/laws/uploads/{uid}/withdraw", headers=LGL1).json()["status"] == "draft"
    _submit(client, uid)
    assert client.post(f"/api/v1/laws/uploads/{uid}/return", headers=LGL2, json={}).status_code == 400
    v = client.post(f"/api/v1/laws/uploads/{uid}/return", headers=LGL2, json={"note": "ข้อ 3 ตกคำว่า ภายใน"}).json()
    assert v["status"] == "draft" and v["review_note"].startswith("ข้อ 3") and v["reviewed_by"] == "lgl2"
    client.post(f"/api/v1/laws/uploads/{uid}/reprocess", headers=LGL1)
    kinds = [r["kind"] for r in client.get(f"/api/v1/laws/uploads/{uid}/revisions", headers=LGL1).json()["items"]]
    assert kinds == ["extract", "edit", "reprocess"]   # ร่างที่แก้ไว้ยังอยู่ในประวัติ
    df = client.get(f"/api/v1/laws/uploads/{uid}/diff", headers=LGL1).json()
    assert df["base"]["kind"] == "reprocess" and df["provisions"] == [] and df["fields"] == []


def test_correction_of_published_instrument(client):
    from sqlalchemy import text

    from app.db import get_engine
    uid = _upload(client, effective_date="2026-03-02").json()["upload_id"]
    _submit(client, uid)
    eli = client.post(f"/api/v1/laws/uploads/{uid}/approve", headers=LGL2, json={}).json()["eli_id"]

    # ตัวบทจากชุดข้อมูลตั้งต้นขอแก้ทางเว็บไม่ได้
    with get_engine().connect() as conn:
        seeded = conn.execute(text("SELECT eli_id FROM legal_instrument WHERE eli_id <> :e LIMIT 1"), {"e": eli}).scalar()
    assert client.post("/api/v1/laws/uploads/corrections", headers=LGL1, json={"eli": seeded}).status_code == 409

    c = client.post("/api/v1/laws/uploads/corrections", headers=ADM, json={"eli": eli})
    assert c.status_code == 200, c.text
    cid = c.json()["upload_id"]
    assert c.json()["amends_eli"] == eli and c.json()["status"] == "draft"
    assert client.post("/api/v1/laws/uploads/corrections", headers=LGL1, json={"eli": eli}).status_code == 409  # ค้างอยู่แล้ว
    assert client.post(f"/api/v1/laws/uploads/{cid}/reprocess", headers=ADM).status_code == 409

    provs = _provs(c.json()["draft"])
    provs[2]["text"] = provs[2]["text"].replace(UNIQUE, "ทดสอบคำแก้ไขหลังเผยแพร่")
    client.put(f"/api/v1/laws/uploads/{cid}", headers=ADM, json={"provisions": provs})
    df = client.get(f"/api/v1/laws/uploads/{cid}/diff", headers=ADM).json()
    assert df["base"]["kind"] == "correction" and [p["key"] for p in df["provisions"]] == ["ข้อ 3"]
    _submit(client, cid, headers=ADM)
    assert client.post(f"/api/v1/laws/uploads/{cid}/approve", headers=ADM, json={}).status_code == 403
    a = client.post(f"/api/v1/laws/uploads/{cid}/approve", headers=LGL1, json={})
    assert a.status_code == 200 and a.json()["status"] == "approved"
    assert client.get(f"/api/v1/laws/uploads/{uid}", headers=LGL1).json()["status"] == "superseded"

    def body():
        with get_engine().connect() as conn:
            return conn.execute(text("""SELECT pv.body_text FROM provision_version pv JOIN provision p USING (provision_id)
                                        WHERE p.eli_id = :p"""), {"p": f"{eli}/clause-3"}).scalars().all()
    assert len(body()) == 1 and "ทดสอบคำแก้ไขหลังเผยแพร่" in body()[0]
    items = client.get("/api/v1/laws/search", params={"q": "ทดสอบคำแก้ไขหลังเผยแพร่"}, headers=LGL1).json()["items"]
    assert items and items[0]["eli_id"].startswith(eli)

    from app.seed import seed   # seed ใหม่ → ใช้ฉบับแก้ไข ไม่ใช่ฉบับเดิม
    with get_engine().begin() as conn:
        seed.run(conn)
    assert len(body()) == 1 and "ทดสอบคำแก้ไขหลังเผยแพร่" in body()[0]
