"""OCR ไฟล์ PDF สแกน/ข้อความเพี้ยน ของทะเบียนที่ยังใช้บังคับ → $LEGAL_IMPORT_WORK/ocr/<sha256>.txt

  python tools/legal_import/ocr.py                     # Tesseract (tha+eng) ในเครื่อง — ค่าเริ่มต้น ไม่ส่งไฟล์ออกนอกเครื่อง
  python tools/legal_import/ocr.py --workers 4 --only 1.2,1.3
  python tools/legal_import/ocr.py --engine endpoint   # โมเดล OCR แบบ OpenAI-compatible vision (เช่น Typhoon OCR บน vLLM)
       OCR_BASE_URL=http://ocr:8000/v1 OCR_MODEL=typhoon-ocr-7b [OCR_API_KEY=…]
       endpoint ภายนอก (คลาวด์) ต้องใส่ --allow-external และส่งได้เฉพาะประกาศ/ระเบียบที่เป็นข้อมูลสาธารณะ —
       ระเบียบบริหารงานภายใน (บุคคล เงินเดือน ฯลฯ) ไม่ถูกส่งออกเด็ดขาด

ผลลัพธ์รักษาย่อหน้า: บรรทัดที่ย่อหน้าเข้า (จากตำแหน่งบรรทัดใน TSV) ขึ้นต้นด้วยช่องว่าง เพื่อให้ convert_bulk แยกย่อหน้า/ข้อได้
แบบเดียวกับ pdftotext -layout; บรรทัดขยะจากตราประทับ/ลายเซ็น (ความมั่นใจต่ำ) ถูกตัดทิ้ง
ข้อความ OCR ต้องตรวจทานเสมอ — convert_bulk ใส่ข้อสังเกต "ถอดจาก OCR" ในประเด็นรอตรวจของทุกฉบับ
"""
import argparse
import base64
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

WORK = Path(os.environ.get("LEGAL_IMPORT_WORK", "work"))
INV = WORK / "inventory.json"
OUT = WORK / "ocr"
DPI = 300
INDENT_PX = 40          # ช่องว่างนำหน้า 1 ตัวต่อระยะเยื้อง 40 px (ที่ 300 dpi) — convert_bulk ถือ ≥ 4 ตัวเป็นย่อหน้าใหม่
MIN_CONF = 45           # บรรทัดสั้นที่ความมั่นใจเฉลี่ยต่ำกว่านี้ = ขยะจากตราประทับ/ลายเซ็น
THAI = re.compile(r"[ก-๙]")


# ---------------------------------------------------------------- targets
def targets(only: set[str] | None) -> list[dict]:
    """PDF ของแถวที่ใช้บังคับ ยังไม่มีตัวบท และไม่มีไฟล์ข้อความที่ใช้ได้ (สแกน หรือ thai_chars ต่ำ/เพี้ยน)"""
    sys.path.insert(0, str(Path(__file__).parent))
    from convert_bulk import best_file, category_of, paragraphs, tone_ratio  # noqa: E402

    inv = json.loads(INV.read_text(encoding="utf-8"))
    out, seen = [], set()
    for r in inv["rows"]:
        if r["status"] != "IN_FORCE" or r["instrument_eli"] or not r["files"]:
            continue
        if only and r["reg_key"] not in only:
            continue
        f = best_file(r["files"])
        if f is not None and (f["ext"] != ".pdf" or tone_ratio("\n".join(paragraphs(f))) >= 0.06):
            continue  # มีข้อความที่ใช้ได้แล้ว
        for p in r["files"]:
            if p["ext"] == ".pdf" and p["sha256"] not in seen:
                seen.add(p["sha256"])
                out.append({"reg": r["reg_key"], "sha256": p["sha256"], "disk": p["disk"], "pages": p["pages"] or 0,
                            "internal": category_of(r["title_th"]) == "การบริหารองค์กร", "title": r["title_th"][:80]})
    return out


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


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["tesseract", "endpoint"], default="tesseract")
    ap.add_argument("--allow-external", action="store_true", help="อนุญาต endpoint ภายนอก (เฉพาะเอกสารสาธารณะ)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--only", help="เลขทะเบียนคั่นด้วย ,")
    ap.add_argument("--force", action="store_true", help="ทำซ้ำแม้มีผลแล้ว")
    ap.add_argument("--list", action="store_true", help="แสดงรายการไฟล์ที่จะ OCR แล้วจบ")
    ap.add_argument("--reclean", action="store_true", help="ใช้ตัวกรองบรรทัดขยะและการแก้คำล่าสุดกับผลที่มีอยู่ (ไม่ OCR ใหม่)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.reclean:
        for p in sorted(OUT.glob("*.txt")):
            lines = [ln for ln in p.read_text(encoding="utf-8").split("\n") if ln.strip() == "\f" or not is_noise(ln)]
            p.write_text(clean("\n".join(lines)), encoding="utf-8")
        print("reclean", len(list(OUT.glob("*.txt"))))
        return
    todo = targets(set(a.only.split(",")) if a.only else None)
    engine = ocr_tesseract
    if a.engine == "endpoint":
        external = _external(os.environ["OCR_BASE_URL"])
        if external and not a.allow_external:
            sys.exit("OCR_BASE_URL เป็นบริการภายนอก — ต้องใส่ --allow-external (ส่งได้เฉพาะเอกสารสาธารณะ)")
        if external:
            held = [t for t in todo if t["internal"]]
            todo = [t for t in todo if not t["internal"]]
            print(f"งดส่งเอกสารภายใน {len(held)} ไฟล์ไปบริการภายนอก — ใช้ --engine tesseract สำหรับไฟล์เหล่านี้")
        engine = ocr_endpoint
    if not a.force:
        todo = [t for t in todo if not (OUT / f"{t['sha256']}.txt").exists()]
    print(f"OCR {len(todo)} ไฟล์ {sum(t['pages'] for t in todo)} หน้า ({a.engine}, {a.workers} workers)")
    if a.list:
        for t in todo:
            print(t["reg"], t["pages"], "ภายใน" if t["internal"] else "", t["title"])
        return

    def one(t: dict) -> str:
        try:
            txt = clean(engine(t["disk"], t["pages"]))
            (OUT / f"{t['sha256']}.txt").write_text(txt, encoding="utf-8")
            return f"✓ {t['reg']} {t['pages']} หน้า {len(THAI.findall(txt))} อักษรไทย"
        except Exception as e:  # noqa: BLE001
            return f"✗ {t['reg']} {type(e).__name__}: {e}"[:300]

    # ไฟล์ใหญ่ก่อน ให้ worker จบพร้อม ๆ กัน
    with ThreadPoolExecutor(a.workers) as ex:
        for msg in ex.map(one, sorted(todo, key=lambda t: -t["pages"])):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
