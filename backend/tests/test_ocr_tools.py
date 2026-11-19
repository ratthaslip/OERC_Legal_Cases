"""เครื่องมือ OCR (tools/legal_import/ocr.py): รักษาการเยื้องย่อหน้า ตัดบรรทัดขยะ และแก้วรรณยุกต์ที่ OCR ตกหล่นแบบไม่เดาคำ"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "legal_import"))
import ocr  # noqa: E402

HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def _tsv(lines):
    out = HEAD
    for i, (left, conf) in enumerate(lines, 1):
        out += f"5\t1\t1\t1\t{i}\t1\t{left}\t{i * 50}\t300\t40\t{conf}\tคำ\n"
    return out


def test_layout_keeps_indent_and_drops_stamp_noise():
    txt = "ข้อ ๑ ผู้รับใบอนุญาตต้องยื่นคำขอ\nภายในสามสิบวัน\na ม ณิ\n    ข้อ ๒ ให้สำนักงานตรวจสอบ\n"
    tsv = _tsv([(300, 92), (150, 90), (900, 20), (300, 91)])
    lines = ocr._layout_lines(txt, tsv)
    assert lines == ["    ข้อ ๑ ผู้รับใบอนุญาตต้องยื่นคำขอ", "ภายในสามสิบวัน", "    ข้อ ๒ ให้สำนักงานตรวจสอบ"]


def test_layout_falls_back_when_counts_differ():
    assert ocr._layout_lines("ก\nข\n", _tsv([(100, 90)])) == ["ก", "ข"]


def test_clean_repairs_only_unambiguous_forms():
    assert ocr.clean("การรับซือไฟฟ้าในพื้นทีโรงไฟฟ้า ดังต่อไปนี") == "การรับซื้อไฟฟ้าในพื้นที่โรงไฟฟ้า ดังต่อไปนี้"
    assert ocr.clean("ยืนข้อเสนอ แต่ยืนยันราคา") == "ยื่นข้อเสนอ แต่ยืนยันราคา"
    assert ocr.clean("ให้มีผลทันที ภายในสิบนาที บางที") == "ให้มีผลทันที ภายในสิบนาที บางที"   # คำที่ถูกต้องไม่ถูกแก้
    assert ocr.clean("สํานักงาน WA. ๒๕๕๕") == "สำนักงาน พ.ศ. ๒๕๕๕"
    assert ocr.clean("ที่ ทีม") == "ที่ ทีม"


def test_external_endpoint_detection():
    assert ocr._external("https://api.opentyphoon.ai/v1")
    assert not ocr._external("http://ocr:8000/v1") and not ocr._external("http://10.1.2.3/v1")
    assert not ocr._external("http://host.docker.internal:11434/v1")


def test_compare_metrics_penalise_broken_thai():
    import ocr_compare
    good = "    ข้อ ๑ ประกาศนี้ให้ใช้บังคับ\n    ข้อ ๒ ผู้รับใบอนุญาตต้องรายงาน\n    ข้อ ๓ ให้ประธานรักษาการ"
    bad = "    ข้อ ๑ ประกาศนีให้ใช้บังคับ ่่\n    ข้อ ๕ ผู้รับใบอนุญาตต้องรายงาน WAเรือง\n    ข้อ ๓ ให้ประธานรักษาการ"
    g, b = ocr_compare.metrics(good), ocr_compare.metrics(bad)
    assert g["invalid_per_1k"] == 0 and b["invalid_per_1k"] > 0
    assert b["repairs_per_1k"] > g["repairs_per_1k"] and b["latin_in_thai_per_1k"] > 0
    assert g["clause_coverage"] == 1.0 and g["renumbered"] == 0 and b["renumbered"] >= 1
