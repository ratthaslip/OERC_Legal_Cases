"""OCR เอกสารกฎหมายภาษาไทย (Tesseract tha+eng ในเครื่อง หรือโมเดล OCR แบบ OpenAI-compatible vision)

ใช้ร่วมกันโดย tools/legal_import/ocr.py (ชุดไฟล์จากฝ่ายกฎหมาย) และหน้านำเข้าเอกสารบนเว็บ (law/uploads.py)
ผลลัพธ์รักษาการเยื้องบรรทัด (ช่องว่างนำหน้า) เพื่อให้ docparse.layout_paragraphs แยกย่อหน้า/ข้อได้
"""
import base64
import csv
import io
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

DPI = 300
INDENT_PX = 40          # ช่องว่างนำหน้า 1 ตัวต่อระยะเยื้อง 40 px (ที่ 300 dpi) — convert_bulk ถือ ≥ 4 ตัวเป็นย่อหน้าใหม่
MIN_CONF = 45           # บรรทัดสั้นที่ความมั่นใจเฉลี่ยต่ำกว่านี้ = ขยะจากตราประทับ/ลายเซ็น
THAI = re.compile(r"[ก-๙]")


# ---------------------------------------------------------------- tesseract
def _layout_lines(txt: str, tsv: str) -> list[str]:
    """จับคู่บรรทัดของผล txt (ตัดคำ/เว้นวรรคแบบ tesseract) กับกรอบบรรทัดใน TSV (ตำแหน่ง + ความมั่นใจ)"""
    words: dict[tuple, list] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") == "5" and row["text"].strip():
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
            words.setdefault(key, []).append((int(row["left"]), float(row["conf"])))
    boxes = [(min(w[0] for w in ws), sum(w[1] for w in ws) / len(ws)) for _, ws in sorted(words.items())]
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    if len(boxes) != len(lines):          # จับคู่ไม่ได้ (ไม่ควรเกิด) → คืนข้อความเดิม
        return lines
    margin = sorted(b[0] for b in boxes)[len(boxes) // 10] if boxes else 0
    out = []
    for ln, (left, conf) in zip(lines, boxes):
        s = ln.strip()
        thai = len(THAI.findall(s))
        if conf < MIN_CONF and (len(s) < 30 or thai < len(s) * 0.4):
            continue
        if is_noise(s):
            continue
        out.append(" " * max(0, round((left - margin) / INDENT_PX)) + s)
    return out


def is_noise(s: str) -> bool:
    """บรรทัดขยะ: สระบน/วรรณยุกต์ที่ OCR แยกเป็นบรรทัดของตัวเอง ("a  ๑7  a  7 ;  a Ce"), เส้น/จุด, ตราประทับ"""
    s = s.strip()
    toks = s.split()
    if not toks:
        return True
    thai = len(THAI.findall(s))
    if thai == 0 and len(s) <= 3:
        return True
    short = sum(1 for t in toks if len(t) <= 2)
    return len(toks) >= 3 and short / len(toks) >= 0.7 and thai < len(s.replace(" ", "")) * 0.6


def ocr_tesseract(pdf: str, pages: int) -> str:
    texts = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-png", pdf, f"{tmp}/p"], check=True, capture_output=True)
        imgs = sorted(Path(tmp).glob("p-*.png"), key=lambda p: int(p.stem.split("-")[1]))
        for img in imgs:
            base = img.with_suffix("")
            subprocess.run(["tesseract", str(img), str(base), "-l", "tha+eng", "--psm", "4", "txt", "tsv"],
                           check=True, capture_output=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
            texts.append("\n".join(_layout_lines(base.with_suffix(".txt").read_text(encoding="utf-8"),
                                                 base.with_suffix(".tsv").read_text(encoding="utf-8"))))
    return "\n\f\n".join(texts)


# ---------------------------------------------------------------- endpoint (vision LLM)
def _external(url: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return not (h in ("localhost", "host.docker.internal") or "." not in h or h.endswith((".local", ".internal", ".lan"))
                or re.match(r"^(10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", h))


def ocr_endpoint(pdf: str, pages: int) -> str:
    import httpx
    base, model, key = os.environ["OCR_BASE_URL"].rstrip("/"), os.environ["OCR_MODEL"], os.environ.get("OCR_API_KEY", "")
    texts = []
    with tempfile.TemporaryDirectory() as tmp, httpx.Client(timeout=300) as c:
        subprocess.run(["pdftoppm", "-r", "200", "-png", pdf, f"{tmp}/p"], check=True, capture_output=True)
        for img in sorted(Path(tmp).glob("p-*.png"), key=lambda p: int(p.stem.split("-")[1])):
            b64 = base64.b64encode(img.read_bytes()).decode()
            r = c.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"} if key else {}, json={
                "model": model, "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "ถอดข้อความภาษาไทยในภาพนี้ตามต้นฉบับทุกตัวอักษร เป็นข้อความล้วน "
                                             "ขึ้นบรรทัดใหม่ตามย่อหน้า ย่อหน้าที่เยื้องให้นำหน้าด้วยช่องว่าง 4 ตัว ไม่ต้องอธิบาย"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]})
            r.raise_for_status()
            texts.append(r.json()["choices"][0]["message"]["content"].strip())
    return "\n\f\n".join(texts)


# ---------------------------------------------------------------- post-processing
# วรรณยุกต์/สระบนที่ OCR ตกหล่นบ่อย — แก้เฉพาะรูปที่ไม่ใช่คำในภาษาไทย หรือมีบริบทกำกับ (ไม่เดาคำทั่วไป)
REPAIRS = [
    (re.compile(r"ซือ|ซ่อ(?=ไฟ)|(?<=รับ)ซื่อ|ซื่อ(?=ไฟ|ขาย)"), "ซื้อ"),
    (re.compile(r"(?<!นา)(?<!ทัน)(?<!บาง)(?<!ทุก)(?<!อีก)ที(?![่้๊๋ม]|ละ|เดียว|วี)(?=[ก-ฮเแโใไ\s(]|$)"), "ที่"),
    (re.compile(r"เริม"), "เริ่ม"),
    (re.compile(r"ยืน(?=ข้อ|คำ|เรื่อง|ต่อ|อุทธรณ์|ขอ|แบบ|เอกสาร|หนังสือ|ภายใน)"), "ยื่น"),
    (re.compile(r"\bW\.?A\.(?=\s*[๐-๙\d])"), "พ.ศ."),
    (re.compile(r"ตังแต่"), "ตั้งแต่"),
    # วัดจากผล OCR ชุดแรก: รูปที่พบบ่อยและไม่ใช่คำ (หรือไม่ใช่คำในบริบทกฎหมาย)
    (re.compile(r"เรือง"), "เรื่อง"),
    (re.compile(r"(?m)^(\s*)(?:ขอ|ซ้อ|ช้อ|ข๋อ)(?=\s*[๐-๙\d])"), r"\1ข้อ"),   # "ขอ ๑๕" ต้นบรรทัด = ข้อ ๑๕
    (re.compile(r"(ติด|จัด|แต่ง|ก่อ)ตัง(?!ค์)"), r"\1ตั้ง"),
    (re.compile(r"คุณะ"), "คณะ"),
    (re.compile(r"ซึง"), "ซึ่ง"),
    (re.compile(r"เพือ"), "เพื่อ"),
    (re.compile(r"เมือ(?![งก])"), "เมื่อ"),
    (re.compile(r"สินสุด"), "สิ้นสุด"),
    (re.compile(r"ทัง(?!ส)"), "ทั้ง"),
    (re.compile(r"นัน(?![ทด])"), "นั้น"),
    (re.compile(r"ไห้"), "ให้"),
    (re.compile(r"(ไปนี|ฉบับนี|ประกาศนี|ระเบียบนี|ทั้งนี|เช่นนี|ดังนี|กรณีนี|ข้อนี)(?![้่])"), r"\1้"),
    (re.compile(r"(?<=ให้)เลือน|เลือน(?=กำหนด|การ|วัน)"), "เลื่อน"),
    (re.compile(r"ใช้บังดับ"), "ใช้บังคับ"),
]


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("ํา", "ำ")  # ํ + า → ำ
    for rx, rep in REPAIRS:
        text = rx.sub(rep, text)
    return text

