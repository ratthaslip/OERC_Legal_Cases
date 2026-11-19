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
