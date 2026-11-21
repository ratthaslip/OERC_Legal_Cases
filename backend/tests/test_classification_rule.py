"""ชั้นข้อมูล (ภายใน/สาธารณะ) ต้องไม่เปลี่ยนตามการแก้หมวดหมู่ — ตรึงผลของกฎกับตัวบทจริงใน repo"""
import json
from pathlib import Path

import pytest

from app.law import docparse

SEED = Path(__file__).resolve().parents[1] / "app" / "seed" / "laws"


def _seed_instruments():
    for f in sorted(SEED.rglob("*.json")):
        yield from json.loads(f.read_text(encoding="utf-8"))


def test_every_public_instrument_in_repo_stays_public():
    """ตัวบทใน repo เป็นสาธารณะทั้งหมด (ฉบับภายในอยู่เฉพาะเครื่องสำนักงาน) — กฎต้องไม่จัดฉบับใดเป็นภายใน"""
    flipped = [i["id"] for i in _seed_instruments()
               if i.get("classification", "public") == "public" and docparse.is_internal_title(i["title"], i.get("registerTitle"))]
    assert flipped == []


def test_classification_independent_of_category_list(monkeypatch):
    titles = [i["title"] for i in _seed_instruments()] + [
        "ระเบียบสำนักงานคณะกรรมการกำกับกิจการพลังงาน ว่าด้วยการพัสดุ พ.ศ. 2560",
        "ระเบียบคณะกรรมการกำกับกิจการพลังงาน ว่าด้วยเบี้ยประชุมคณะอนุกรรมการ พ.ศ. 2555"]
    before = [docparse.is_internal_title(t) for t in titles]
    monkeypatch.setattr(docparse, "CATEGORIES", [("หมวดใหม่ทั้งหมด", r"ระเบียบ|ประกาศ")])   # ฝ่ายกฎหมายปรับหมวดใหม่
    assert [docparse.is_internal_title(t) for t in titles] == before


@pytest.mark.parametrize("title, reg, internal", [
    ("ระเบียบสำนักงานคณะกรรมการกำกับกิจการพลังงาน ว่าด้วยการพัสดุ พ.ศ. 2560", None, True),
    ("ระเบียบคณะกรรมการกำกับกิจการพลังงาน ว่าด้วยการพัสดุของกองทุนพัฒนาไฟฟ้า พ.ศ. 2555", None, False),
    ("ลกพ. ทดสอบ", "ระเบียบว่าด้วยค่าตอบแทนกรรมการ", True),       # ชื่อย่อในทะเบียนไม่ทำให้หลุดเป็นสาธารณะ
    ("ประกาศ เรื่อง การรับซื้อไฟฟ้าจากพลังงานหมุนเวียน", None, False),
])
def test_rule_examples(title, reg, internal):
    assert docparse.is_internal_title(title, reg) is internal


def test_public_title_with_internal_word_gets_review_note():
    paras = ["ระเบียบคณะกรรมการกำกับกิจการพลังงาน", "ว่าด้วยการพัสดุของกองทุนพัฒนาไฟฟ้า พ.ศ. 2555",
             "ข้อ 1 ระเบียบนี้เรียกว่า ระเบียบทดสอบ", "ข้อ 2 ให้ใช้บังคับตั้งแต่วันถัดจากวันประกาศ"]
    inst, _why, warn = docparse.build_instrument(
        paras, doc_type="ระเบียบ", reg_title="ระเบียบ กกพ. ว่าด้วยการพัสดุของกองทุนพัฒนาไฟฟ้า", key="t-fund",
        effective_date="2012-01-01", gaz=None, pub=None, ocr=False, ext=".docx", short="ทดสอบ", source_default="x")
    assert inst and "classification" not in inst and inst["category"] == "กองทุนพัฒนาไฟฟ้า"
    assert any("พัสดุ" in w and "ตรวจชั้นข้อมูล" in w for w in warn)
