"""แปลงไฟล์กฎหมายลำดับรองจากฝ่ายกฎหมาย (Word/PDF มีข้อความ) → seed/laws/legal-team/*.json

ขอบเขตชุดที่ 1: แถวทะเบียนที่ "ใช้บังคับ" มีไฟล์ข้อความใช้ได้ ยังไม่มีตัวบทในระบบ และมีเอกสารเดียวในแถว
ฉบับแก้ไขเพิ่มเติมนำเข้าเป็นตราสารแยก (ยังไม่ประกอบเข้าฉบับหลัก — ระบบแสดงคำเตือนที่ฉบับหลัก)
ผลลัพธ์อื่น: $LEGAL_IMPORT_WORK/convert_report.json (ข้าม/เตือนรายแถว)
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/ → import app.law.docparse
from app.law.docparse import (build_instrument, doc_paragraphs, docx_paragraphs, fix_lost_am,  # noqa: E402
                              gazette_from_text, layout_paragraphs, pdf_paragraphs, tone_ratio)

WORK = Path(os.environ.get("LEGAL_IMPORT_WORK", "work"))
INV = WORK / "inventory.json"
OUT = Path(__file__).resolve().parents[2] / "app" / "seed" / "laws" / "legal-team"
# ตราสารภายใน (หมวดบริหารองค์กร) ไม่อยู่ใน repo — เขียนแยกไว้ แล้วนำไปวางที่ LAWS_INTERNAL_DIR บนเครื่องของสำนักงาน
INTERNAL_OUT = Path(os.environ.get("LEGAL_IMPORT_INTERNAL_OUT", str(WORK / "laws-internal"))) / "legal-team"
REPORT = WORK / "convert_report.json"
SOURCE = "ไฟล์จากฝ่ายกฎหมาย สำนักงาน กกพ."


# ผลจาก ocr.py (ข้อความที่รักษาการเยื้องบรรทัด) — เลือกชุดของ engine อื่นด้วย LEGAL_IMPORT_OCR_DIR=ocr-akson
OCR_DIR = WORK / os.environ.get("LEGAL_IMPORT_OCR_DIR", "ocr")


def ocr_text(f: dict) -> str | None:
    p = OCR_DIR / f"{f['sha256']}.txt"
    return p.read_text(encoding="utf-8") if f["ext"] == ".pdf" and p.exists() else None




def best_file(files: list[dict], use_ocr: bool = False) -> dict | None:
    for ext in (".docx", ".doc"):
        c = [f for f in files if f["ext"] == ext and f["thai_chars"] > 300]
        if c:
            return max(c, key=lambda f: f["thai_chars"])
    c = [f for f in files if f["ext"] == ".pdf" and not f["scanned"] and f["thai_chars"] > 300]
    if c:
        return max(c, key=lambda f: f["thai_chars"])
    return ocr_file(files) if use_ocr else None


def ocr_file(files: list[dict]) -> dict | None:
    """PDF ที่มีผล OCR แล้ว (ฉบับที่ได้อักษรไทยมากสุด) — ใช้เมื่อไม่มีไฟล์ข้อความ หรือข้อความจาก PDF เพี้ยน"""
    c = [(f, len(THAI_RE.findall(t))) for f in files for t in [ocr_text(f)] if t]
    c = [x for x in c if x[1] > 300]
    return {**max(c, key=lambda x: x[1])[0], "ocr": True} if c else None


THAI_RE = re.compile(r"[ก-๙]")


def paragraphs(f: dict) -> list[str]:
    if f.get("ocr"):
        ps = layout_paragraphs(ocr_text(f))
    else:
        ps = {".docx": docx_paragraphs, ".doc": doc_paragraphs}.get(f["ext"], pdf_paragraphs)(f["disk"])
    joined = fix_lost_am("\n".join(ps))
    return joined.split("\n")



def gazette(files: list[dict]) -> tuple[str | None, str | None]:
    for f in files:
        src, pub = gazette_from_text((WORK / "text" / f"{f['sha256']}.txt").read_text(encoding="utf-8"))
        if src:
            return src, pub
    return None, None


def main() -> None:
    inv = json.loads(INV.read_text(encoding="utf-8"))
    for d in (OUT, INTERNAL_OUT):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.json"):
            f.unlink()
    rep = {"converted": [], "skipped": []}
    used_eli = set()
    done_sha: dict[str, str] = {}
    for r in sorted(inv["rows"], key=lambda r: r["sheet_row"]):
        why = None
        if r["status"] != "IN_FORCE":
            why = "ยกเลิกแล้ว (ชุดถัดไป)"
        elif r["instrument_eli"]:
            why = "มีตัวบทในระบบแล้ว"
        elif not r["files"]:
            why = "ไม่มีไฟล์"
        elif len({re.sub(r"^\d+\s*\.\s*", "", f["name"].rsplit(".", 1)[0]) for f in r["files"]}) > 1:
            why = "มีมากกว่า 1 เอกสารในแถว — รอตรวจการจับคู่"
        f = best_file(r["files"], use_ocr=True) if not why else None
        if not why and not f:
            why = "มีแต่ PDF สแกน (รอ OCR)"
        if why:
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": why, "title": r["title_th"][:90]})
            continue
        if f["sha256"] in done_sha:  # ทะเบียนหลายแถวชี้ไฟล์เดียวกัน → ตราสารเดียว (ผูกแถวอื่นด้วยชื่อเรื่อง)
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": f"ไฟล์เดียวกับ {done_sha[f['sha256']]}",
                                   "title": r["title_th"][:90]})
            continue
        done_sha[f["sha256"]] = f"ทะเบียน {r['reg_key']}"
        kind = "มาตรา" if r["doc_type"] in ("พระราชกฤษฎีกา", "พระราชบัญญัติ") else "ข้อ"
        paras = paragraphs(f)
        if tone_ratio("\n".join(paras)) < 0.06 and not f.get("ocr") and (g := ocr_file(r["files"])):
            f, paras = g, paragraphs(g)   # ข้อความจาก PDF เพี้ยน (ฟอนต์เก่า) → ใช้ผล OCR
        # ข้อความเพี้ยนจากฟอนต์เก่ามีวรรณยุกต์น้อยมาก; ผล OCR ที่ดีแต่มีตาราง/ตัวเลขมากก็ต่ำกว่า 0.06 ได้ จึงใช้เกณฑ์ต่ำกว่า
        if tone_ratio("\n".join(paras)) < (0.03 if f.get("ocr") else 0.06):
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": "ข้อความจาก PDF เพี้ยน (รอ OCR)",
                                   "title": r["title_th"][:90]})
            continue
        gaz, pub = gazette(r["files"])
        short = re.sub(r"^\d+\s*\.\s*", "", f["name"].rsplit(".", 1)[0]).strip()[:100]
        inst, why, warn = build_instrument(
            paras, doc_type=r["doc_type"], reg_title=r["title_th"], key="g" + r["reg_key"].replace(".", "-"),
            effective_date=r["effective_date"], gaz=gaz, pub=pub, ocr=bool(f.get("ocr")), ext=f["ext"], short=short,
            used_eli=used_eli, dup_suffix=f"-r{r['sheet_row']}", source_default=SOURCE)
        if inst is None:
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": why, "title": r["title_th"][:90]})
            continue
        eli = inst["id"]
        notes = inst.pop("importNotes")
        inst["sourceFiles"] = [{"sha256": x["sha256"], "name": x["name"],
                                "role": "text" if x["sha256"] == f["sha256"] else "copy"} for x in r["files"]]
        inst["importNotes"] = notes
        fn = (INTERNAL_OUT if inst.get("classification") == "internal" else OUT) / (eli.split("/", 2)[2].replace("/", "_") + ".json")
        fn.write_text(json.dumps([inst], ensure_ascii=False, indent=1), encoding="utf-8")
        rep["converted"].append({"reg": r["reg_key"], "eli": eli, "provisions": len(inst["provisions"]),
                                 "src": "ocr" if f.get("ocr") else f["ext"],
                                 "warn": warn, "title": inst["title"][:90]})
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("converted", len(rep["converted"]), "skipped", len(rep["skipped"]))


if __name__ == "__main__":
    main()
