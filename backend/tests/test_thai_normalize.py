"""ปรับคำถาม/เอกสารภาษาไทยก่อนค้น: คำย่อ, เลขเป็นคำ, โทเค็นแนวคิด และการแยกวรรค"""
from app.law.fragments import best, split, with_labels
from app.search.thai import content_terms, tokenize


def test_abbreviation_with_dots_matches_full_name():
    full = set(tokenize("คณะกรรมการกำกับกิจการพลังงาน"))
    assert full <= set(tokenize("กกพ. สั่ง"))
    assert full <= set(tokenize("สำนักงาน กกพ"))
    assert "พระราชกฤษฎีกา" in tokenize("พ.ร.ฎ.")


def test_thai_number_words_become_digits():
    assert "30" in tokenize("ภายในสามสิบวัน")
    assert "120" in tokenize("หนึ่งร้อยยี่สิบวัน")
    assert "21" in tokenize("ยี่สิบเอ็ดวัน")
    assert "5000" in tokenize("ฉบับละ ๕,๐๐๐ บาท")
    assert not any(t.isdigit() for t in tokenize("สามีภรรยา"))


def test_quantity_question_meets_quantity_text():
    assert "qtyday" in content_terms("ต้องวินิจฉัยภายในกี่วัน")
    assert "qtyday" in tokenize("ให้แล้วเสร็จภายในระยะเวลาหกสิบวันนับแต่วันที่รับเรื่อง")
    assert "qtybaht" in content_terms("ค่าธรรมเนียมต่ออายุเท่าไร")
    assert "qtybaht" in tokenize("การต่ออายุใบอนุญาต ฉบับละ ๕,๐๐๐ บาท")


def test_effective_and_list_concepts():
    assert "qtyeffective" in content_terms("ระเบียบนี้ใช้บังคับเมื่อใด")
    assert "qtyeffective" in tokenize("ระเบียบนี้ให้ใช้บังคับเมื่อพ้นกำหนดหนึ่งร้อยยี่สิบวัน")
    assert "qtylist" in content_terms("ทำได้ด้วยวิธีใดบ้าง")
    assert "qtylist" in tokenize("อาจใช้วิธีการ ดังต่อไปนี้\n(๑) การสำรวจ")
    assert "qtylist" not in tokenize("ให้เป็นไปตามระเบียบ")


S121 = """ในกรณีที่ผู้ใช้พลังงานไม่พอใจคำสั่ง ให้มีสิทธิอุทธรณ์ต่อคณะกรรมการได้
การอุทธรณ์ตามวรรคหนึ่งให้ยื่นต่อคณะกรรมการภายในสามสิบวัน
หลักเกณฑ์ให้เป็นไปตามระเบียบ ดังต่อไปนี้
(๑) วิธีการยื่น
(ก) ยื่นด้วยตนเอง
การอุทธรณ์ตามวรรคสอง ย่อมไม่เป็นการทุเลาการบังคับตามคำสั่ง"""


def test_split_paragraphs_and_items():
    ids = [(f.id, f.label) for f in split(S121)]
    assert ids == [("para-1", "วรรคหนึ่ง"), ("para-2", "วรรคสอง"), ("para-3", "วรรคสาม"),
                   ("para-3/item-1", "วรรคสาม (1)"), ("para-3/item-1/sub-ก", "วรรคสาม (1) (ก)"),
                   ("para-4", "วรรคสี่")]


def test_best_fragment_and_labels():
    assert best(S121, content_terms("อุทธรณ์ทำให้ทุเลาการบังคับหรือไม่")).id == "para-4"
    assert best("ข้อความวรรคเดียว", {"ข้อความ"}) is None
    assert with_labels(S121).splitlines()[3].startswith("[วรรคสาม (1)] (๑)")
