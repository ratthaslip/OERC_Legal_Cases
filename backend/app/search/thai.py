"""ตัดคำภาษาไทยสำหรับ full-text search

Postgres ไม่มี parser ภาษาไทย จึงตัดคำด้วย PyThaiNLP ก่อน แล้วเก็บเป็น tsvector ด้วย config 'simple'
ใช้ฟังก์ชันเดียวกันทั้งตอน index และตอนค้น (SDD 6.3)
"""
import json
import re
import unicodedata
from pathlib import Path

from pythainlp.tokenize import word_tokenize

LEXICON_PATH = Path(__file__).with_name("lexicon.json")


def _load_lexicon() -> dict:
    data = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    return {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
            for k, v in data.items() if isinstance(v, dict)}


_LEX = _load_lexicon()
ABBREVIATIONS = _LEX.get("abbreviations", {})   # คำย่อมีจุด → คำเต็ม (แทนก่อนตัดคำ ทั้งเอกสารและคำถาม)
SYNONYMS = _LEX.get("synonyms", {})             # คำหลังตัดคำ → รูปมาตรฐาน
QUERY_EXPANSIONS = _LEX.get("query_expansions", {})  # คำเรียกทั่วไป → ถ้อยคำในชื่อกฎหมาย (คำถามเท่านั้น)
# ไม่ติดอักษรไทยหน้า/หลัง ("กกพ." ใน "สำนักงาน กกพ." ได้ แต่ไม่ไปจับกลางคำอื่น); จุดท้ายไม่บังคับ
_ABBR_RE = [(re.compile(r"(?<![ก-ฮ])" + re.escape(k).replace(r"\.", r"\.?") + r"(?![ก-ฮ])"), f" {v} ")
            for k, v in sorted(ABBREVIATIONS.items(), key=lambda kv: -len(kv[0]))]

STOPWORDS = {"และ", "หรือ", "ของ", "ที่", "ใน", "การ", "ความ", "ได้", "ให้", "เป็น", "ว่า", "จาก", "โดย", "แห่ง", "ซึ่ง", "อยู่"}
# คำที่ใช้ตั้งคำถาม ไม่ใช่เนื้อหา — ไม่นับตอนวัดความครอบคลุมของหลักฐาน
QUESTION_WORDS = {"เปรียบเทียบ", "แนว", "เรื่อง", "อะไร", "อย่างไร", "ไหม", "บ้าง", "สรุป", "ขอ", "ช่วย", "คือ",
                  "มี", "กรณี", "หรือไม่", "เกี่ยวกับ", "ตาม", "ทำ", "ไร", "ยังไง", "ต้อง", "กี่", "วัน", "หลักเกณฑ์"}
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_THAI_DIGITS)
    return text.replace("\u200b", "")


_NUM_WORD = re.compile(r"^(?:ศูนย์|หนึ่ง|เอ็ด|สอง|ยี่|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|ร้อย|พัน|หมื่น|แสน|ล้าน)+$")
_UNITS = {"วัน": "day", "วันทำการ": "day", "เดือน": "month", "ปี": "year", "บาท": "baht"}

# โทเค็นแนวคิด: ให้คำถามแบบ "กี่วัน / เท่าไร / ใช้บังคับเมื่อใด / มีอะไรบ้าง" จับคู่กับถ้อยคำที่ตอบคำถามแบบนั้น
# (ข้อความในเอกสารเขียนเลขเป็นคำ เช่น "ภายในหกสิบวัน" ซึ่งไม่มีคำใดตรงกับ "กี่วัน" เลย)
CONCEPT_PREFIX = "qty"
_DOC_EFFECTIVE = re.compile(r"(?:ให้|มีผล)ใช้บังคับ(?:ตั้งแต่|เมื่อ|นับแต่)")
_Q_EFFECTIVE = re.compile(r"(?:ใช้บังคับ|มีผล|บังคับใช้)(?:ตั้งแต่)?(?:เมื่อใด|เมื่อไร|เมื่อไหร่|วันใด|วันไหน)|เริ่มใช้(?:บังคับ)?เมื่อ")
_DOC_LIST = re.compile(r"ดังต่อไปนี้\s*\n\s*\(")
_Q_LIST = re.compile(r"(?:ใด|อะไร|ไหน)บ้าง|มีกี่(?:วิธี|ประเภท|กรณี|ขั้นตอน)")
_Q_AMOUNT = re.compile(r"เท่าไร|เท่าใด|เท่าไหร่")


def expand_abbreviations(text: str) -> str:
    for rx, full in _ABBR_RE:
        text = rx.sub(full, text)
    return text


def _concepts(raw: list[str], text: str) -> list[str]:
    out: list[str] = []
    for i, tok in enumerate(raw):
        unit = _UNITS.get(raw[i + 1]) if i + 1 < len(raw) else None
        if unit and (tok.isdigit() or _NUM_WORD.match(tok) or tok == "กี่"):
            out.append(CONCEPT_PREFIX + unit)
    if _Q_AMOUNT.search(text):
        out.append(CONCEPT_PREFIX + ("day" if "ระยะเวลา" in text else "baht"))
    if _DOC_EFFECTIVE.search(text) or _Q_EFFECTIVE.search(text):
        out.append(CONCEPT_PREFIX + "effective")
    if _DOC_LIST.search(text) or _Q_LIST.search(text):
        out.append(CONCEPT_PREFIX + "list")
    return list(dict.fromkeys(out))


def _thai_number(tok: str) -> str | None:
    try:
        from pythainlp.util import thaiword_to_num
        return str(thaiword_to_num(tok))
    except Exception:
        return None


def term_overlap(q_terms: set[str], text: str) -> float:
    """สัดส่วนคำค้นที่พบในข้อความ (0–1)"""
    return len(q_terms & set(tokenize(text))) / (len(q_terms) or 1)


def tokenize(text: str) -> list[str]:
    text = expand_abbreviations(normalize(text))
    raw: list[str] = []
    for tok in word_tokenize(text, engine="newmm", keep_whitespace=False):
        tok = re.sub(r"[^\w\u0E00-\u0E7F]", "", tok).lower()  # \w ไม่รวมสระ/วรรณยุกต์ไทย
        if tok:
            raw.append(tok)
    out: list[str] = []
    run: list[str] = []
    for tok in [*raw, ""]:
        if tok and _NUM_WORD.match(tok):
            run.append(tok)
        elif run:                                     # "หนึ่งร้อย" "ยี่สิบ" → เพิ่ม "120" ให้ตรงกับคำถามที่พิมพ์เป็นเลข
            n = _thai_number("".join(run))
            if n:
                out.append(n)
            run = []
        if not tok or tok in STOPWORDS:
            continue
        out.append(SYNONYMS.get(tok, tok))
    return out + _concepts(raw, text)


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


def expand_query(q: str) -> str:
    """แทนคำเรียกทั่วไปด้วยถ้อยคำในชื่อกฎหมาย เช่น 'โซลาร์รูฟท็อป' → 'พลังงานแสงอาทิตย์ ติดตั้งบนหลังคา'
    (แทนที่ ไม่ต่อท้าย: คำเรียกเองไม่มีในเอกสาร ถ้าเก็บไว้จะดึงคะแนนความครอบคลุมลง)"""
    out = normalize(q)
    for k in sorted(QUERY_EXPANSIONS, key=len, reverse=True):  # คำยาวก่อน: โซลาร์รูฟท็อป ก่อน โซลาร์
        v = QUERY_EXPANSIONS[k]
        pat = rf"(?i)\b{re.escape(k)}\b" if k.isascii() else re.escape(k)
        out = re.sub(pat, f" {v} ", out)
    return re.sub(r"\s+", " ", out).strip()
