"""ตัดคำภาษาไทยสำหรับ full-text search

Postgres ไม่มี parser ภาษาไทย จึงตัดคำด้วย PyThaiNLP ก่อน แล้วเก็บเป็น tsvector ด้วย config 'simple'
ใช้ฟังก์ชันเดียวกันทั้งตอน index และตอนค้น (SDD 6.3)
"""
import re
import unicodedata

from pythainlp.tokenize import word_tokenize

# คำย่อ/คำพ้องที่พบบ่อยในงาน กกพ. → รูปมาตรฐาน (ขยายได้จากข้อมูลจริง)
SYNONYMS = {
    "กกพ": "คณะกรรมการกำกับกิจการพลังงาน",
    "พรบ": "พระราชบัญญัติ",
    "อธ": "อุทธรณ์",
    "ม": "มาตรา",
}
STOPWORDS = {"และ", "หรือ", "ของ", "ที่", "ใน", "การ", "ความ", "ได้", "ให้", "เป็น", "ว่า", "จาก", "โดย", "แห่ง", "ซึ่ง", "อยู่"}
# คำที่ใช้ตั้งคำถาม ไม่ใช่เนื้อหา — ไม่นับตอนวัดความครอบคลุมของหลักฐาน
QUESTION_WORDS = {"เปรียบเทียบ", "แนว", "เรื่อง", "อะไร", "อย่างไร", "ไหม", "บ้าง", "สรุป", "ขอ", "ช่วย", "คือ",
                  "มี", "กรณี", "หรือไม่", "เกี่ยวกับ", "ตาม", "ทำ", "ไร", "ยังไง", "ต้อง", "กี่", "วัน", "หลักเกณฑ์"}
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_THAI_DIGITS)
    return text.replace("​", "")


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for tok in word_tokenize(normalize(text), engine="newmm", keep_whitespace=False):
        tok = re.sub(r"[^\w\u0E00-\u0E7F]", "", tok).lower()  # \w ไม่รวมสระ/วรรณยุกต์ไทย
        if not tok or tok in STOPWORDS:
            continue
        out.append(SYNONYMS.get(tok, tok))
    return out


def to_document(text: str) -> str:
    """ข้อความที่ส่งเข้า to_tsvector('simple', …)"""
    return " ".join(tokenize(text))


def to_query(text: str) -> str:
    """tsquery แบบ OR ระหว่างคำ (จัดอันดับด้วย ts_rank_cd) — ใช้ quote กัน syntax error"""
    toks = list(dict.fromkeys(tokenize(text)))
    return " | ".join("'" + t.replace("'", "") + "'" for t in toks)


REF_PATTERNS = [
    re.compile(r"(อธ|รร|คม|พ|อ)\.?\s*(\d{1,4})\s*/\s*(\d{4})"),  # เลขอุทธรณ์/คดี
]


def normalize_ref_no(text: str) -> str | None:
    """'อธ. 104/2567' / 'อธ104/2567' → 'อธ104/2567' สำหรับจับคู่เลขที่ตรงตัว"""
    m = REF_PATTERNS[0].search(normalize(text))
    if not m:
        return None
    return f"{m.group(1)}{int(m.group(2))}/{m.group(3)}"


def content_terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in QUESTION_WORDS and len(t) > 1}
