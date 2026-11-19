"""Pipeline tests on synthetic text only — no office data is needed or committed."""
from erc_corpus.segment import flatten, parse
from erc_corpus.textnorm import normalize

REG = """หน้า ๑
เล่ม ๑๒๖ ตอนพิเศษ ๓ ง ราชกิจจานุเบกษา ๘ มกราคม ๒๕๕๒
ระเบียบคณะกรรมการกำกับกิจการพลังงาน
ว่าด้วยการขอรับใบอนุญาต
พ.ศ. ๒๕๕๑
อาศัยอำนาจตามความในมาตรา ๕๐ แห่งพระราชบัญญัติการประกอบกิจการพลังงาน พ.ศ. ๒๕๕๐
ซึ่งมาตรา ๒๙ ประกอบกับมาตรา ๓๓ มาตรา ๔๑ มาตรา ๔๒ และมาตรา ๔๓
ของรัฐธรรมนูญ คณะกรรมการออกระเบียบไว้ ดังต่อไปนี้
ข้อ ๑ ระเบียบนี้เรียกว่า “ระเบียบทดสอบ”
ข้อ ๒ ระเบียบนี้ให้ใช้บังคับตั้งแต่วันถัดจากวันประกาศในราชกิจจานุเบกษาเป็นต้นไป
ข้อ ๓ ให้เพิ่มความต่อไปนี้
“ข้อ ๙ ข้อความที่อยู่ในเครื่องหมายคำพูด”
ข้อ ๔ ภายในสี่สิบห้าวันตามข้อ ๓
ข้อ ๓ ที่ขึ้นบรรทัดใหม่แต่เป็นการอ้างถึง
ประกาศ ณ วันที่ ๒๕ ธันวาคม พ.ศ. ๒๕๕๑
"""


def test_normalize_repairs_split_sara_am_and_pua():
    assert normalize("คณะกรรมการก ากับ ส าหรับ น้ า") == "คณะกรรมการกำกับ สำหรับ น้ำ"
    assert normalize("") == "่"  # PUA tone mark


def test_reg_units_skip_cross_refs_and_quotes():
    meta, units = parse(REG, "T220")
    ids = [u["id"] for u in units if u["kind"] == "ข้อ"]
    assert ids == ["T220@2551#ข้อ1", "T220@2551#ข้อ2", "T220@2551#ข้อ3", "T220@2551#ข้อ4"]
    assert meta["unit_kind"] == "ข้อ"  # the มาตรา lines in the preamble are not units
    assert "ข้อ ๙" in units[[u["unit"] for u in units].index("ข้อ3")]["text"]


def test_act_footnote_marker_is_stripped():
    act = "พระราชบัญญัติ ทดสอบ พ.ศ. ๒๕๕๐\nมาตรา ๑ ชื่อ\nมาตรา ๒๑ ใช้บังคับ\nมาตรา ๓ ข้อยกเว้น\n"
    _, units = parse(act, "ACT2550")
    assert [u["unit"] for u in units if u["kind"] == "มาตรา"] == ["ม1", "ม2", "ม3"]


def test_flatten_keeps_paragraph_breaks():
    t = "มาตรา ๑ ข้อความยาวยาวยาวยาวยาวยาวยาว\nต่อบรรทัดยาวยาวยาวยาวยาวยาวยาวยาวยาว\nจบวรรค\nวรรคสองยาวยาวยาวยาวยาวยาวยาวยาวยาว"
    assert flatten(t) == "มาตรา ๑ ข้อความยาวยาวยาวยาวยาวยาวยาวต่อบรรทัดยาวยาวยาวยาวยาวยาวยาวยาวยาวจบวรรค วรรคสองยาวยาวยาวยาวยาวยาวยาวยาวยาว"
