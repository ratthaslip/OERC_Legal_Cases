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
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/ → import app.law.ocr
from app.law.ocr import (THAI, _external, _layout_lines, clean, is_noise, ocr_endpoint,  # noqa: E402,F401
                         ocr_tesseract)

WORK = Path(os.environ.get("LEGAL_IMPORT_WORK", "work"))
INV = WORK / "inventory.json"
OUT = WORK / "ocr"


# ---------------------------------------------------------------- targets
def targets(only: set[str] | None) -> list[dict]:
    """PDF ของแถวที่ใช้บังคับ ยังไม่มีตัวบท และไม่มีไฟล์ข้อความที่ใช้ได้ (สแกน หรือ thai_chars ต่ำ/เพี้ยน)"""
    sys.path.insert(0, str(Path(__file__).parent))
    from convert_bulk import best_file, paragraphs  # noqa: E402

    from app.law.docparse import category_of, tone_ratio

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
