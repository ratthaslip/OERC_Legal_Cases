"""ตัวนำเข้าทะเบียนกฎหมายลำดับรอง — ใช้ workbook สังเคราะห์ (ไม่ใช้ข้อมูลจริงของสำนักงาน)"""
from datetime import date

import openpyxl
import pytest

from app.law.register import normalize_status, parse_sections, parse_thai_date, read_workbook
from app.search.thai import expand_query


def test_parse_thai_date():
    assert parse_thai_date("6 มกราคม 2566") == date(2023, 1, 6)
    assert parse_thai_date("๑๒ มีนาคม ๒๕๖๐\n(เมื่อพ้น ๖๐ วัน)") == date(2017, 3, 12)
    assert parse_thai_date("๑ มกราคม พ.ศ. ๒๕๖๕") == date(2022, 1, 1)
    assert parse_thai_date("16 มิถุนายน -2565") == date(2022, 6, 16)
    assert parse_thai_date("ยกเว้นตามมาตรา 29 (6)") is None
    assert parse_thai_date("๒๔ กันยาบน ๒๕๕๓") is None  # สะกดเดือนผิด → ไม่เดา


def test_parse_sections():
    assert parse_sections("11 (1) (4)") == [11]
    assert parse_sections("14 วรรคหก") == [14]
    assert parse_sections("33, 50") == [33, 50]
    assert parse_sections("๑๒๑") == [121]
    assert parse_sections(None) == []


def test_status():
    assert normalize_status("ใช้บังคับ", None) == "IN_FORCE"
    assert normalize_status(None, "ถูกยกเลิกแล้ว") == "REPEALED"
    assert normalize_status(None, None) == "UNKNOWN"


def test_expand_query():
    assert expand_query("ระเบียบโซลาร์รูฟท็อป") == "ระเบียบ พลังงานแสงอาทิตย์ ติดตั้งบนหลังคา"
    assert "ผู้ผลิตไฟฟ้าขนาดเล็กมาก" in expand_query("VSPP")
    assert expand_query("มาตรา 75") == "มาตรา 75"


@pytest.fixture
def workbook(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ฐานข้อมูล"
    ws["A2"] = "ทะเบียนคุมทดสอบ"
    ws.append([])
    ws.append(["ลำดับ", "ประเภท", "รายชื่อกฎหมาย", "บทอาศัยอำนาจ", "ฝ่ายงาน\nผู้รับผิดชอบ", "นิติกร\nผู้รับผิดชอบ",
               "วันใช้บังคับ", "รอบการประเมินผลสัมฤทธิ์ \n(ครบ 5 ปี)", "ปีงบประมาณ", "รับฟังหลักการ", "รับฟังร่างกฎหมาย",
               "สถานะปัจจุบัน", "Link ระบบกลาง", "หมายเหตุ"])
    ws.append([1, "ระเบียบ/ประกาศ", "หัวกลุ่มทดสอบ"])
    ws.append([1.1, "ระเบียบ", "ระเบียบ กกพ. ว่าด้วยเรื่องทดสอบ พ.ศ. ๒๕๖๐", "11 (1)", "กฟ.", "ชื่อบุคคล",
               "๑ มกราคม ๒๕๖๑", None, None, None, None, "ใช้บังคับ"])
    ws.append([1.9, "ประกาศ", "ประกาศ กกพ. เรื่อง ทดสอบ", "33", "บม.", "ชื่อบุคคล", "2 มกราคม 2562"])
    ws.append([1.1, "ระเบียบ", "ประกาศ กกพ. เรื่อง ประเภทผิด", "33", "บม.", None, "ยกเว้นตามมาตรา 29 (6)",
               None, None, None, None, None, None, "ถูกยกเลิกแล้ว"])
    st = wb.create_sheet("สถิติ")
    st.append([])
    st.append([])
    st.append([])
    st.append(["ลำดับ", "ประเภท", "รายชื่อกฎหมาย", "บทอาศัยอำนาจ", "กฎหมาย\nPDF", "กฎหมาย\nWORD"])
    st.append([1.1, "ระเบียบ", "ระเบียบ กกพ. ว่าด้วยเรื่องทดสอบ พ.ศ. ๒๕๖๐", "11 (1)", 1, 2])
    p = tmp_path / "reg.xlsx"
    wb.save(p)
    return p


def test_read_workbook(workbook):
    items, title = read_workbook(workbook)
    assert title == "ทะเบียนคุมทดสอบ"
    head, a, b, c = items
    assert head.status == "GROUP" and not head.issues
    assert (a.reg_no, a.status, a.authority_secs, a.effective_date) == ("1.1", "IN_FORCE", [11], date(2018, 1, 1))
    assert a.attachments == {"pdf": 1, "word": 2}
    assert not hasattr(a, "lawyer")  # ไม่นำเข้าชื่อนิติกร
    assert "ชื่อบุคคล" not in repr(a)
    assert b.status == "UNKNOWN" and "ไม่ระบุสถานะปัจจุบัน" in b.issues
    # 1.1 ที่ตามหลัง 1.9 = Excel แปลง "1.10" เป็นตัวเลข
    assert c.reg_no == "1.10" and any("Excel" in i for i in c.issues)
    assert c.status == "REPEALED"
    assert "วันใช้บังคับไม่อยู่ในรูปแบบวันที่" in c.issues
    assert any("ไม่ตรงกับชื่อเรื่อง" in i for i in c.issues)


def test_register_api_permissions(workbook):
    """ประชาชนเห็นชื่อ/สถานะ/บทอาศัยอำนาจ แต่ไม่เห็นฝ่ายงานและประเด็นข้อมูล; ค้นจากชื่อได้; ผู้ช่วย AI ใช้เป็นแหล่งได้"""
    import os
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash", REGISTER_XLSX=str(workbook))
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        assert seed.run(conn)["register_rows"] == 4
    c = TestClient(app)
    pub = c.get("/api/v1/laws/register", headers={"X-Demo-User": "pub"}).json()
    assert pub["total"] == 3  # ไม่นับแถวหัวกลุ่ม
    assert "owner_unit" not in pub["items"][0] and "issues" not in pub["items"][0]
    assert "units" not in pub["facets"]
    staff = c.get("/api/v1/laws/register", params={"issues_only": True}, headers={"X-Demo-User": "lgl1"}).json()
    assert staff["total"] == 2 and "owner_unit" in staff["items"][0]
    hit = c.get("/api/v1/laws/register", params={"q": "เรื่องทดสอบ"}, headers={"X-Demo-User": "pub"}).json()
    assert hit["items"][0]["title_th"].startswith("ระเบียบ กกพ. ว่าด้วยเรื่องทดสอบ")
    rid = hit["items"][0]["register_id"]
    d = c.get(f"/api/v1/laws/register/{rid}", headers={"X-Demo-User": "pub"}).json()
    assert d["group_title"] == "หัวกลุ่มทดสอบ" and len(d["related"]) == 2
    sec = c.get("/api/v1/laws/register", params={"section": 33}, headers={"X-Demo-User": "pub"}).json()
    assert sec["total"] == 2


def test_apply_amendments(tmp_path):
    """ฉบับแก้ไข: เพิ่มข้อใหม่ + ยกเลิกความเดิมแล้วใช้ความใหม่ → รุ่นตามวันที่"""
    import importlib.util
    import json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("apply_amendments", Path(__file__).parents[2] / "tools" / "apply_amendments.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def v(text, vf):
        return [{"version": 1, "validFrom": vf, "validTo": None, "text": text, "amendingInstrument": "x", "summary": None, "relations": []}]
    base = [{"id": "eli/b", "provisions": [
        {"id": "eli/b/clause-1", "number": "ข้อ 1", "part": None, "versions": v("ชื่อ", "2010-01-01")},
        {"id": "eli/b/clause-2", "number": "ข้อ 2", "part": "หมวด ๑", "versions": v("เดิม", "2010-01-01")},
        {"id": "eli/b/clause-3", "number": "ข้อ 3", "part": "หมวด ๑", "versions": v("จะถูกยกเลิก", "2010-01-01")}]}]
    amend = [{"id": "eli/a", "code": "แก้ 1", "title": "ฉบับที่ ๒", "source": "ราชกิจจาฯ", "effectiveDate": "2015-06-27",
              "provisions": [
                  {"number": "ข้อ 1", "versions": v("ระเบียบนี้เรียกว่า", "2015-06-27")},
                  {"number": "ข้อ 3", "versions": v("ให้เพิ่มข้อความต่อไปนี้เป็นข้อ ๒/๑ ของระเบียบ\n“ข้อ ๒/๑ ใหม่”", "2015-06-27")},
                  {"number": "ข้อ 4", "versions": v("ให้ยกเลิกความในข้อ ๒ ของระเบียบ และให้ใช้ความต่อไปนี้แทน\n“ข้อ ๒ ความใหม่\nวรรคสอง”", "2015-06-27")},
                  {"number": "ข้อ 5", "versions": v("ให้ยกเลิกข้อ ๓ ของระเบียบ", "2015-06-27")}]}]
    bp, ap = tmp_path / "b.json", tmp_path / "a.json"
    bp.write_text(json.dumps(base, ensure_ascii=False)); ap.write_text(json.dumps(amend, ensure_ascii=False))
    mod.main(str(bp), [str(ap)])
    out = {p["number"]: p for p in json.loads(bp.read_text())[0]["provisions"]}
    assert list(out) == ["ข้อ 1", "ข้อ 2", "ข้อ 2/1", "ข้อ 3"]
    assert [(x["validFrom"], x["validTo"]) for x in out["ข้อ 2"]["versions"]] == [("2010-01-01", "2015-06-26"), ("2015-06-27", None)]
    assert out["ข้อ 2"]["versions"][1]["text"] == "ความใหม่\nวรรคสอง"
    assert out["ข้อ 2/1"]["versions"][0]["validFrom"] == "2015-06-27"
    assert out["ข้อ 3"]["versions"][0]["validTo"] == "2015-06-26"
    mod.main(str(bp), [str(ap)])  # นำไปใช้ซ้ำต้องไม่เปลี่ยนอะไร
    assert len(json.loads(bp.read_text())[0]["provisions"]) == 4
