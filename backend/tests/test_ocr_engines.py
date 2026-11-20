"""OCR engine ทางเลือก (AksonOCR-Lite, OpenThai2.0-OCR-Text): แปลงผลบรรทัด+กรอบเป็นข้อความที่รักษาย่อหน้า
และเลือก engine ผ่าน OCR_ENGINE — เทสต์ด้วยผลจำลอง ไม่ต้องติดตั้งโมเดล"""
from types import SimpleNamespace as NS

import pytest

from app.law import docparse, ocr

W = 2480  # A4 ที่ 300 dpi


def _ln(text, x, conf=0.95):
    return NS(text=text, bbox=[x, 0, x + 1500, 40], confidence=conf, poly=None)


def test_akson_layout_keeps_paragraphs_and_skips_headers():
    lines = [_ln("ราชกิจจานุเบกษา หน้า ๑๒", 900), _ln("ข้อ ๑ ประกาศนี้เรียกว่า ประกาศทดสอบ", 400),
             _ln("ต่อเนื่องบรรทัดสอง", 250), _ln("ข้อ ๒ ให้ใช้บังคับตั้งแต่วันถัดจากวันประกาศ", 400),
             _ln("ตราประทับ", 1800, 0.3)]
    blocks = [NS(label="header", order=0, line_ids=[0], text=None, html=None, bbox=None),
              NS(label="text", order=1, line_ids=[1, 2], text=None, html=None, bbox=None),
              NS(label="text", order=2, line_ids=[3], text=None, html=None, bbox=None),
              NS(label="seal", order=3, line_ids=[4], text=None, html=None, bbox=None),
              NS(label="table", order=4, line_ids=[], text=None, bbox=None,
                 html="<table><tr><td>ลำดับ</td><td>อัตรา</td></tr><tr><td>1</td><td>0.50 บาท</td></tr></table>")]
    out = ocr.akson_page_layout(NS(lines=lines, blocks=blocks, width=W))
    assert not any("ราชกิจจา" in x or "ตราประทับ" in x for x in out)
    assert out[0].startswith("    ข้อ ๑") and out[1] == "ต่อเนื่องบรรทัดสอง"
    assert "ลำดับ | อัตรา" in "\n".join(out) and "1 | 0.50 บาท" in out[-1]
    paras = docparse.layout_paragraphs("\n".join(out))
    assert paras[0] == "ข้อ ๑ ประกาศนี้เรียกว่า ประกาศทดสอบต่อเนื่องบรรทัดสอง" or paras[0].startswith("ข้อ ๑")
    assert any(p.startswith("ข้อ ๒") for p in paras)


def test_openthai_layout_with_polygon_boxes_and_low_confidence():
    r = {"text": "", "lines": [
        {"text": "ข้อ ๓ ผู้รับใบอนุญาตต้องรายงาน", "confidence": 0.97, "row": 0, "box": [[400, 0], [1900, 0], [1900, 40], [400, 40]]},
        {"text": "ภายในสามสิบวัน", "confidence": 0.93, "row": 1, "box": [250, 50, 900, 90]},
        {"text": "a ม", "confidence": 0.2, "row": 2, "box": [2000, 100, 2100, 130]}]}
    out = ocr.openthai_page_layout(r, W)
    assert out == ["    ข้อ ๓ ผู้รับใบอนุญาตต้องรายงาน", "ภายในสามสิบวัน"]


def test_engine_selection(monkeypatch):
    monkeypatch.setenv("OCR_ENGINE", "openthai")
    assert ocr.engine_name() == "openthai"
    monkeypatch.setenv("OCR_ENGINE", "magic")
    with pytest.raises(ocr.OcrUnavailable):
        ocr.engine_name()
    monkeypatch.delenv("OCR_ENGINE")
    assert ocr.engine_name() == "tesseract"


def test_missing_engine_gives_clear_message(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "_openthai", None)
    monkeypatch.setenv("OPENTHAI_OCR_DIR", str(tmp_path))
    with pytest.raises(ocr.OcrUnavailable, match="OPENTHAI_OCR_DIR"):
        ocr._openthai_engine()


def test_table_rows_unescape():
    assert ocr._table_rows("<tr><th>ก&amp;ข</th><td> 1 </td></tr>") == ["ก&ข | 1"]


def test_openthai_joins_boxes_on_the_same_row():
    r = {"lines": [
        {"text": "ข้อ ๔", "confidence": 0.99, "row": 0, "box": [400, 0, 520, 40]},
        {"text": "ให้สำนักงานตรวจสอบ", "confidence": 0.96, "row": 0, "box": [560, 0, 1500, 40]},
        {"text": "ภายในเจ็ดวัน", "confidence": 0.95, "row": 1, "box": [250, 50, 800, 90]}]}
    assert ocr.openthai_page_layout(r, W) == ["    ข้อ ๔ ให้สำนักงานตรวจสอบ", "ภายในเจ็ดวัน"]


def test_short_clause_heading_kept_despite_low_confidence_or_skip_label():
    lines = [_ln("ข้อ ๒ ประกาศนี้ให้ใช้บังคับ", 400), _ln("ข้อ ๓ ในประกาศนี้", 400, 0.3),
             _ln("ข้อ ๔ ให้การไฟฟ้ารับซื้อ", 400)]
    blocks = [NS(label="text", order=0, line_ids=[0], text=None, html=None, bbox=None),
              NS(label="number", order=1, line_ids=[1], text=None, html=None, bbox=None),
              NS(label="text", order=2, line_ids=[2], text=None, html=None, bbox=None)]
    out = ocr.akson_page_layout(NS(lines=lines, blocks=blocks, width=W))
    assert [x.strip() for x in out] == ["ข้อ ๒ ประกาศนี้ให้ใช้บังคับ", "ข้อ ๓ ในประกาศนี้", "ข้อ ๔ ให้การไฟฟ้ารับซื้อ"]
