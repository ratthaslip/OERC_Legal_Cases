"""OCR เอกสารกฎหมายภาษาไทย — ทุกตัวรันในเครื่อง (ยกเว้น endpoint ที่ผู้ดูแลกำหนดเอง)

เลือกด้วย OCR_ENGINE:
  tesseract (ค่าเริ่มต้น) — Tesseract tha+eng
  akson     — AksonOCR-Lite (Remtir, Apache-2.0, ONNX บน CPU, มี layout/ตาราง)   pip install <github.com/Remtir/AksonOCR-Lite zip>
              AKSONOCR_WEIGHTS=<โฟลเดอร์โมเดลจาก huggingface Remtir/AksonOCR-Lite>
  openthai  — OpenThai2.0-OCR-Text (iApp, Apache-2.0, ONNX/OpenVINO บน CPU)     OPENTHAI_OCR_DIR=<โฟลเดอร์โมเดล>

ใช้ร่วมกันโดย tools/legal_import/ocr.py (ชุดไฟล์จากฝ่ายกฎหมาย) และหน้านำเข้าเอกสารบนเว็บ (law/uploads.py)
ผลลัพธ์รักษาการเยื้องบรรทัด (ช่องว่างนำหน้า) เพื่อให้ docparse.layout_paragraphs แยกย่อหน้า/ข้อได้
"""
import base64
import csv
import html
import io
import os
import re
import subprocess
import sys
import tempfile
import threading
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
        for img in _pdf_pages(pdf, tmp):
            if img is None:
                texts.append(SKIPPED_PAGE)
                continue
            base = img.with_suffix("")
            subprocess.run(["tesseract", str(img), str(base), "-l", "tha+eng", "--psm", "4", "txt", "tsv"],
                           check=True, capture_output=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
            texts.append("\n".join(_layout_lines(base.with_suffix(".txt").read_text(encoding="utf-8"),
                                                 base.with_suffix(".tsv").read_text(encoding="utf-8"))))
    return "\n\f\n".join(texts)


# ---------------------------------------------------------------- เครื่องมือร่วม: บรรทัด + กรอบ → ข้อความที่รักษาการเยื้อง
def _left(box) -> float:
    """ขอบซ้ายของกรอบ: รองรับ [x0, y0, x1, y1] และ [[x, y] × 4] (polygon)"""
    if box is None:
        return 0.0
    if len(box) and isinstance(box[0], (list, tuple)):
        return float(min(p[0] for p in box))
    return float(box[0])


CLAUSE_HEAD = re.compile(r"^\s*(ข้อ|มาตรา)\s*[๐-๙\d]")


def lines_to_layout(items: list[tuple[str, float, float | None]], width: float,
                    min_conf: float = 0.5, para_starts: set[int] | None = None) -> list[str]:
    """[(ข้อความ, ขอบซ้าย, ความมั่นใจ 0–1)] ตามลำดับการอ่าน → บรรทัดที่มีช่องว่างนำหน้าตามระยะเยื้อง
    หน่วยเยื้อง = 1.6% ของความกว้างหน้า (เท่ากับ 40 px ที่ 300 dpi บน A4) ให้ docparse.layout_paragraphs ใช้ได้เหมือน Tesseract
    para_starts: ลำดับบรรทัดที่เป็นต้นย่อหน้า (จาก layout ของ engine) — บังคับเยื้องอย่างน้อย 4"""
    lefts = sorted(x for _, x, _ in items) or [0.0]
    margin = lefts[len(lefts) // 10]
    unit = max(width * 0.016, 1.0)
    out = []
    for i, (text_, left, conf) in enumerate(items):
        t = (text_ or "").strip()
        if not t:
            continue
        thai = len(THAI.findall(t))
        head = bool(CLAUSE_HEAD.match(t))  # หัวข้อ/มาตราสั้น ๆ (เช่น "ข้อ ๓ ในประกาศนี้") ห้ามตัดทิ้ง
        if not head and conf is not None and conf < min_conf and (len(t) < 30 or thai < len(t) * 0.4):
            continue
        if not head and is_noise(t):
            continue
        n = max(0, round((left - margin) / unit))
        if para_starts and i in para_starts:
            n = max(n, 4)
        out.append(" " * n + t)
    return out


PAGE_TIMEOUT = int(os.environ.get("OCR_PAGE_TIMEOUT", "180"))   # วินาทีต่อหน้าสำหรับเรนเดอร์ PDF → ภาพ
FALLBACK_DPI = 150


def pdf_page_count(pdf: str) -> int:
    out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, flags=re.M)
    return int(m.group(1)) if m else 0


def _render_page(pdf: str, n: int, base: str, dpi: int, gray: bool) -> Path | None:
    cmd = ["pdftoppm", "-r", str(dpi), "-f", str(n), "-l", str(n), "-singlefile", "-png"] + (["-gray"] if gray else [])
    try:
        subprocess.run(cmd + [pdf, base], check=True, capture_output=True, timeout=PAGE_TIMEOUT)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None
    img = Path(base + ".png")
    return img if img.exists() else None


def _pdf_pages(pdf: str, tmp: str, dpi: int = DPI, gray: bool = True):
    """เรนเดอร์ทีละหน้า (ไม่เก็บภาพทั้งเล่มไว้พร้อมกัน) — หน้าที่เกิน OCR_PAGE_TIMEOUT ลองใหม่ที่ 150 dpi
    ถ้ายังไม่ได้ ส่ง None (ผู้เรียกใส่หน้าว่าง + หมายเหตุ) แทนการค้างทั้งไฟล์"""
    total = pdf_page_count(pdf)
    for n in range(1, total + 1):
        base = f"{tmp}/p-{n}"
        img = _render_page(pdf, n, base, dpi, gray) or (dpi > FALLBACK_DPI and _render_page(pdf, n, base, FALLBACK_DPI, gray))
        if not img:
            print(f"  ! เรนเดอร์หน้า {n}/{total} ไม่สำเร็จ (เกิน {PAGE_TIMEOUT} s) — ข้ามหน้านี้", file=sys.stderr, flush=True)
        try:
            yield img or None
        finally:
            if img:
                img.unlink(missing_ok=True)


SKIPPED_PAGE = "[หน้านี้เรนเดอร์ไม่สำเร็จ — ต้องตรวจจากต้นฉบับ]"


class OcrUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- AksonOCR-Lite
_akson = None
_akson_lock = threading.Lock()
SKIP_LABELS = {"header", "footer", "number", "page_number", "seal", "image", "figure", "chart", "header_image",
               "footer_image", "aside_text", "footnote"}


def _akson_conv():
    global _akson
    with _akson_lock:
        if _akson is None:
            try:
                import aksonocr_lite
            except ImportError as e:
                raise OcrUnavailable("ยังไม่ได้ติดตั้ง AksonOCR-Lite (pip install https://github.com/Remtir/AksonOCR-Lite/archive/refs/heads/main.zip) — หรือใช้ OCR_ENGINE=tesseract") from e
            _akson = aksonocr_lite.load(device=os.environ.get("AKSON_DEVICE", "cpu"),
                                        dewarp=os.environ.get("AKSON_DEWARP") == "1", tables=True)
    return _akson


def _table_rows(h: str) -> list[str]:
    """HTML ตาราง → บรรทัดละแถว คั่นเซลล์ด้วย " | " (เอกสารแนบท้ายเก็บเป็นข้อความ)"""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, flags=re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def akson_page_layout(page) -> list[str]:
    """Page ของ AksonOCR (blocks ตามลำดับการอ่าน + lines พร้อมกรอบ) → บรรทัดที่รักษาการเยื้อง; ข้ามหัว/ท้ายกระดาษ ตราประทับ รูป"""
    lines = list(page.lines or [])
    blocks = sorted(page.blocks or [], key=lambda b: (b.order if b.order is not None else 10 ** 6))
    width = float(page.width or max((_left(getattr(l, "bbox", None)) for l in lines), default=1000) * 1.2)
    items: list[tuple[str, float, float | None]] = []
    starts: set[int] = set()
    tables: dict[int, list[str]] = {}
    used: set[int] = set()
    for b in blocks:
        ids = [i for i in (b.line_ids or []) if 0 <= i < len(lines)]
        used.update(ids)
        if b.label in SKIP_LABELS:
            # layout อาจจัดบรรทัดหัวข้อสั้น ๆ เป็น number/aside_text — เก็บไว้ถ้าขึ้นต้นด้วย "ข้อ/มาตรา + เลข"
            for i in ids:
                ln = lines[i]
                if CLAUSE_HEAD.match(ln.text or ""):
                    starts.add(len(items))
                    items.append((ln.text, _left(ln.bbox), ln.confidence))
            continue
        if b.label == "table" and getattr(b, "html", None):
            tables[len(items)] = _table_rows(b.html)
            continue
        if ids:
            starts.add(len(items))
            for i in ids:
                ln = lines[i]
                items.append((ln.text, _left(ln.bbox), ln.confidence))
        elif b.text:
            starts.add(len(items))
            x = _left(b.bbox)
            items.extend((t, x, None) for t in b.text.splitlines())
    if not blocks:  # ไม่มี layout → ใช้บรรทัดตามลำดับที่ได้
        items = [(ln.text, _left(ln.bbox), ln.confidence) for ln in lines]
    out = lines_to_layout(items, width, para_starts=starts)
    if tables:  # แทรกตารางตามตำแหน่งโดยประมาณ (ต่อท้ายหน้า) — รูปแบบตารางยังต้องตรวจทาน
        for rows in tables.values():
            out.append("")
            out.extend("    " + r if k == 0 else r for k, r in enumerate(rows))
    return out


def ocr_akson(pdf: str, pages: int = 0) -> str:
    conv = _akson_conv()
    texts = []
    with tempfile.TemporaryDirectory() as tmp:
        for img in _pdf_pages(pdf, tmp):   # เรนเดอร์เองที่ 300 dpi ให้ทุก engine ได้ภาพเดียวกัน
            if img is None:
                texts.append(SKIPPED_PAGE)
                continue
            for page in conv.convert(str(img)):
                texts.append("\n".join(akson_page_layout(page)))
    return "\n\f\n".join(texts)


# ---------------------------------------------------------------- OpenThai2.0-OCR-Text (iApp)
_openthai = None
_openthai_lock = threading.Lock()


def _openthai_engine():
    global _openthai
    with _openthai_lock:
        if _openthai is None:
            d = os.environ.get("OPENTHAI_OCR_DIR", "")
            if not d or not (Path(d) / "det.onnx").exists():
                raise OcrUnavailable("ตั้ง OPENTHAI_OCR_DIR เป็นโฟลเดอร์โมเดล OpenThai2.0-OCR-Text (มี det.onnx rec.onnx runtime/) "
                                     "— หรือใช้ OCR_ENGINE=tesseract")
            sys.path.insert(0, d)
            try:
                from runtime.line_engine import LineOCR  # มากับโมเดล (huggingface iapp/OpenThai2.0-OCR-Text)
            except ImportError as e:
                raise OcrUnavailable(f"โหลด runtime ของ OpenThai OCR ไม่ได้: {e} (ต้องมี openvino หรือ onnxruntime, "
                                     "opencv-python-headless, pyclipper)") from e
            _openthai = LineOCR(d, threads=int(os.environ.get("OPENTHAI_OCR_THREADS", "1")))
    return _openthai


def openthai_page_layout(r: dict, width: float) -> list[str]:
    """ผลของ LineOCR.read (lines: text, confidence, row, box) → บรรทัดที่รักษาการเยื้อง"""
    lines = r.get("lines") or []
    if lines and all("row" in ln for ln in lines):
        # หนึ่งแถวอาจมีหลายกล่อง (เช่น "ข้อ ๑" กับเนื้อความแยกกัน) — รวมเป็นบรรทัดเดียวตามแถว เรียงซ้าย→ขวา
        rows: dict = {}
        for ln in lines:
            rows.setdefault(ln["row"], []).append(ln)
        items = []
        for seg in rows.values():
            seg.sort(key=lambda x: _left(x.get("box")))
            confs = [x.get("confidence") for x in seg if x.get("confidence") is not None]
            items.append((" ".join((x.get("text") or "").strip() for x in seg).strip(), _left(seg[0].get("box")),
                          min(confs) if confs else None))
    else:
        items = [(ln.get("text", ""), _left(ln.get("box")), ln.get("confidence")) for ln in lines]
    if not items and r.get("text"):
        items = [(t, 0.0, None) for t in r["text"].splitlines()]
    return lines_to_layout(items, width)


def ocr_openthai(pdf: str, pages: int = 0) -> str:
    eng = _openthai_engine()
    texts = []
    with tempfile.TemporaryDirectory() as tmp:
        for img in _pdf_pages(pdf, tmp):
            if img is None:
                texts.append(SKIPPED_PAGE)
                continue
            r = eng.read(str(img))
            from PIL import Image
            with Image.open(img) as im:
                w = im.width
            texts.append("\n".join(openthai_page_layout(r, w)))
    return "\n\f\n".join(texts)


ENGINES = {"tesseract": ocr_tesseract, "akson": ocr_akson, "openthai": ocr_openthai}


def engine_name(name: str | None = None) -> str:
    n = (name or os.environ.get("OCR_ENGINE") or "tesseract").lower()
    if n not in ENGINES:
        raise OcrUnavailable(f"OCR_ENGINE ไม่รู้จัก: {n} (ใช้ได้: {', '.join(ENGINES)})")
    return n


def ocr_pdf(pdf: str, engine: str | None = None) -> str:
    """OCR ทั้งไฟล์ด้วย engine ที่เลือก (ข้อความดิบ ยังไม่ผ่าน clean)"""
    return ENGINES[engine_name(engine)](pdf, 0)


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
        for img in _pdf_pages(pdf, tmp, dpi=200, gray=False):
            if img is None:
                texts.append(SKIPPED_PAGE)
                continue
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
    (re.compile(r"(?m)^(\s*)(?:ขอ|ซ้อ|ช้อ|ข๋อ|ขอ้)(?=\s*[๐-๙\d])"), r"\1ข้อ"),   # "ขอ ๑๕" ต้นบรรทัด = ข้อ ๑๕
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

