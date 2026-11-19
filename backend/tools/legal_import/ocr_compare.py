"""เทียบ OCR engine กับไฟล์สแกนจริงของทะเบียน (ไม่ต้องมีเฉลย) — รันบนเซิร์ฟเวอร์ ผลอยู่นอก git

  python tools/legal_import/ocr_compare.py                              # tesseract, akson, openthai ทุกไฟล์
  python tools/legal_import/ocr_compare.py --engines tesseract,openthai --limit 15
  python tools/legal_import/ocr_compare.py --report-only                # สรุปจากผลที่มีอยู่ ไม่ OCR ใหม่
  python tools/legal_import/ocr_compare.py --dir /data/scans            # PDF ในโฟลเดอร์ใดก็ได้

เลือกไฟล์: มี $LEGAL_IMPORT_WORK/inventory.json → ไฟล์สแกนของทะเบียน; ไม่มี (เช่นบนเซิร์ฟเวอร์) → ไฟล์ต้นฉบับของตราสารที่
นำเข้าด้วย OCR จากฐานข้อมูล (raw_file + import_note "ถอดจากภาพสแกนด้วย OCR"); หรือระบุ --dir

ผล: $LEGAL_IMPORT_WORK/ocr-compare/<engine>/<sha>.raw.txt (ก่อน clean) + .txt (หลัง clean)
     $LEGAL_IMPORT_WORK/ocr-compare/report.md, report.json, per_file.csv

ตัวชี้วัด (ยิ่งต่ำยิ่งดี เว้นแต่ระบุ) — วัดจากข้อความดิบก่อนการแก้คำ เพื่อสะท้อนคุณภาพของ engine เอง:
  sec/page            เวลาเฉลี่ยต่อหน้า (รวมเรนเดอร์ PDF → ภาพ 300 dpi)
  invalid/1k          ลำดับอักษรไทยที่ผิดอักขรวิธี (สระบน/วรรณยุกต์ลอย, วรรณยุกต์ซ้อน, สระหน้าไม่มีพยัญชนะตาม) ต่ออักษรไทย 1,000 ตัว
  repairs/1k          จุดที่ต้องใช้กฎแก้คำ (ซือ→ซื้อ, ทัง→ทั้ง, ฯลฯ) ต่ออักษรไทย 1,000 ตัว
  latin_in_thai/1k    อักษรละตินที่แทรกกลางคำไทย (เช่น "WA." แทน "พ.ศ.")
  clause_coverage ↑   สัดส่วนข้อที่แยกได้ / เลขข้อสูงสุดที่พบ (ข้อมูลโครงสร้างครบแค่ไหน)
  renumbered          เลขข้อที่อ่านผิดลำดับ (เลขไทย ๔↔๕↔๖) ต้องเรียงใหม่
  thai_chars ↑        ปริมาณอักษรไทยที่อ่านได้ (ต่ำผิดปกติ = อ่านตกหล่น)
ตัวเลขเหล่านี้เป็นตัวแทนคุณภาพ — ควรสุ่มเทียบกับต้นฉบับด้วยตาอีก 3–5 ไฟล์ก่อนตัดสินใจ
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.law import docparse  # noqa: E402
from app.law.ocr import REPAIRS, THAI, OcrUnavailable, clean, ocr_pdf  # noqa: E402

WORK = Path(os.environ.get("LEGAL_IMPORT_WORK", "work"))
OUT = WORK / "ocr-compare"

INVALID = [
    re.compile(r"(?<![ก-ฮัิ-ฺ็-๎])[ัิ-ฺ็-๎]"),  # สระบน/ล่าง/วรรณยุกต์ไม่มีพยัญชนะนำ
    re.compile(r"[่-๋]{2,}"),                                                       # วรรณยุกต์ซ้อน
    re.compile(r"[เแโใไ](?![ก-ฮ])"),                                                           # สระหน้าไม่มีพยัญชนะตาม
    re.compile(r"ํา"),                                                              # ํ + า แทน ำ
]
LATIN_IN_THAI = re.compile(r"(?<=[ก-๙])[A-Za-z]+|[A-Za-z]+(?=[ก-๙])")


def metrics(raw: str) -> dict:
    thai = len(THAI.findall(raw)) or 1
    per_k = lambda n: round(n * 1000 / thai, 2)  # noqa: E731
    cleaned = clean(raw)
    paras = docparse.fix_lost_am("\n".join(docparse.layout_paragraphs(cleaned))).split("\n")
    renum: list[str] = []
    _, _, provs, *_ = docparse.parse(paras, "ข้อ", "", ocr=True, renumbered=renum)
    seen = [int(docparse.th2ar(m.group(1))) for p in paras for m in [re.match(r"^ข้อ\s*([๐-๙\d]+)\s", p)] if m]
    top = max([x for x in seen if x <= 200], default=0)
    n_clauses = sum(1 for p in provs if p["no"] and p["no"] != "__annex__")
    return {
        "thai_chars": thai,
        "invalid_per_1k": per_k(sum(len(rx.findall(raw)) for rx in INVALID)),
        "repairs_per_1k": per_k(sum(len(rx.findall(raw)) for rx, _ in REPAIRS)),
        "latin_in_thai_per_1k": per_k(len(LATIN_IN_THAI.findall(raw))),
        "clauses": n_clauses, "max_clause_seen": top,
        "clause_coverage": round(min(n_clauses, top) / top, 3) if top else None,
        "renumbered": len(renum),
    }


def _pages(path: str) -> int:
    import subprocess
    out = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, flags=re.M)
    return int(m.group(1)) if m else 0


def files_from_dir(d: str) -> list[dict]:
    import hashlib
    out = []
    for p in sorted(Path(d).rglob("*.pdf")) + sorted(Path(d).rglob("*.PDF")):
        out.append({"reg": p.stem[:40], "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "disk": str(p),
                    "pages": _pages(str(p)), "internal": False})
    return out


def files_from_db() -> list[dict]:
    """ไฟล์ต้นฉบับของตราสารที่นำเข้าด้วย OCR (ใช้บนเซิร์ฟเวอร์ที่ไม่มี inventory.json)"""
    from app.db import get_engine, rows
    from app.storage import RawStore
    store = RawStore()
    with get_engine().connect() as c:
        rs = rows(c, """
            SELECT DISTINCT ON (f.sha256) f.sha256, f.stored_path, i.eli_id, i.classification,
                   COALESCE(r.reg_no, split_part(i.eli_id, '/', 6)) AS reg
            FROM law_open_issue o
            JOIN legal_instrument i ON i.eli_id = o.instrument_eli
            JOIN raw_file_link l ON l.instrument_eli = i.eli_id AND l.role = 'text'
            JOIN raw_file f ON f.sha256 = l.sha256
            LEFT JOIN law_register r ON r.instrument_eli = i.eli_id
            WHERE o.kind = 'import_note' AND o.note LIKE 'ถอดจากภาพสแกนด้วย OCR%' AND lower(f.stored_path) LIKE '%.pdf'""")
    return [{"reg": r["reg"], "sha256": r["sha256"], "disk": str(store.path(r["stored_path"])),
             "pages": _pages(str(store.path(r["stored_path"]))), "internal": r["classification"] != "public"} for r in rs]


def files_by_sha(shas: list[str]) -> list[dict]:
    """ไฟล์ในคลังไฟล์ต้นฉบับตาม sha256 (เช่น ฉบับที่ OCR รอบก่อนอ่านไม่ได้จึงยังไม่ได้นำเข้า)"""
    from app.db import get_engine, rows
    from app.storage import RawStore
    store = RawStore()
    with get_engine().connect() as c:
        rs = rows(c, "SELECT sha256, stored_path, original_name, classification FROM raw_file WHERE sha256 = ANY(:s)", s=shas)
    found = {r["sha256"] for r in rs}
    for s in shas:
        if s not in found:
            print(f"ไม่พบไฟล์ {s[:12]} ในคลังไฟล์ต้นฉบับ")
    return [{"reg": Path(r["original_name"] or r["sha256"]).stem[:40], "sha256": r["sha256"],
             "disk": str(store.path(r["stored_path"])), "pages": _pages(str(store.path(r["stored_path"]))),
             "internal": r["classification"] != "public"} for r in rs]


def run(engines: list[str], limit: int | None, force: bool, src_dir: str | None = None,
        extra: list[str] | None = None) -> None:
    if src_dir:
        todo = files_from_dir(src_dir)
    elif (WORK / "inventory.json").exists():
        from ocr import targets  # tools/legal_import/ocr.py — ไฟล์สแกนของแถวที่ใช้บังคับและยังไม่มีตัวบท
        todo = targets(None)
    else:
        todo = files_from_db()
    if extra:
        have = {t["sha256"] for t in todo}
        todo += [t for t in files_by_sha(extra) if t["sha256"] not in have]
    todo = sorted(todo, key=lambda t: (t["pages"], t["reg"]))  # ไฟล์สั้นก่อน: --limit ได้ผลเร็ว
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} ไฟล์ {sum(t['pages'] for t in todo)} หน้า × {', '.join(engines)}")
    for eng in engines:
        d = OUT / eng
        d.mkdir(parents=True, exist_ok=True)
        for k, t in enumerate(todo, 1):
            raw_p, meta_p = d / f"{t['sha256']}.raw.txt", d / f"{t['sha256']}.json"
            if raw_p.exists() and meta_p.exists() and not force:
                continue
            t0 = time.perf_counter()
            try:
                raw = ocr_pdf(t["disk"], eng)
            except OcrUnavailable as e:
                print(f"ข้าม {eng}: {e}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"✗ {eng} {t['reg']}: {type(e).__name__}: {e}"[:300])
                continue
            sec = time.perf_counter() - t0
            raw_p.write_text(raw, encoding="utf-8")
            (d / f"{t['sha256']}.txt").write_text(clean(raw), encoding="utf-8")
            meta_p.write_text(json.dumps({"reg": t["reg"], "pages": t["pages"], "seconds": round(sec, 2),
                                          "internal": t["internal"]}), encoding="utf-8")
            print(f"[{eng} {k}/{len(todo)}] {t['reg']} {t['pages']} หน้า {sec:.1f} s", flush=True)


def report(engines: list[str]) -> None:
    rows, summary = [], {}
    shas = None
    for eng in engines:  # เทียบเฉพาะไฟล์ที่ทุก engine มีผล
        s = {p.name.split(".")[0] for p in (OUT / eng).glob("*.json")} if (OUT / eng).exists() else set()
        shas = s if shas is None else shas & s
    shas = sorted(shas or [])
    for eng in engines:
        acc = {"files": 0, "pages": 0, "seconds": 0.0, "thai": 0, "inv": 0.0, "rep": 0.0, "lat": 0.0,
               "cov": [], "renum": 0}
        for sha in shas:
            meta = json.loads((OUT / eng / f"{sha}.json").read_text(encoding="utf-8"))
            m = metrics((OUT / eng / f"{sha}.raw.txt").read_text(encoding="utf-8"))
            rows.append({"engine": eng, "reg": meta["reg"], "sha256": sha[:12], "pages": meta["pages"],
                         "sec_per_page": round(meta["seconds"] / max(meta["pages"], 1), 2), **m})
            w = m["thai_chars"]
            acc["files"] += 1
            acc["pages"] += meta["pages"]
            acc["seconds"] += meta["seconds"]
            acc["thai"] += w
            acc["inv"] += m["invalid_per_1k"] * w
            acc["rep"] += m["repairs_per_1k"] * w
            acc["lat"] += m["latin_in_thai_per_1k"] * w
            acc["renum"] += m["renumbered"]
            if m["clause_coverage"] is not None:
                acc["cov"].append(m["clause_coverage"])
        t = acc["thai"] or 1
        summary[eng] = {
            "files": acc["files"], "pages": acc["pages"],
            "sec_per_page": round(acc["seconds"] / max(acc["pages"], 1), 2),
            "thai_chars": acc["thai"],
            "invalid_per_1k": round(acc["inv"] / t, 2), "repairs_per_1k": round(acc["rep"] / t, 2),
            "latin_in_thai_per_1k": round(acc["lat"] / t, 2),
            "clause_coverage": round(sum(acc["cov"]) / len(acc["cov"]), 3) if acc["cov"] else None,
            "renumbered": acc["renum"],
        }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps({"files": len(shas), "summary": summary}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    if rows:
        with open(OUT / "per_file.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    cols = [("sec_per_page", "วินาที/หน้า ↓"), ("thai_chars", "อักษรไทย ↑"), ("invalid_per_1k", "ผิดอักขรวิธี/1k ↓"),
            ("repairs_per_1k", "ต้องแก้คำ/1k ↓"), ("latin_in_thai_per_1k", "ละตินปนไทย/1k ↓"),
            ("clause_coverage", "แยกข้อครบ ↑"), ("renumbered", "เลขข้อผิดลำดับ ↓")]
    md = [f"# เทียบ OCR — {len(shas)} ไฟล์ที่ทุก engine มีผล", "",
          "| ตัวชี้วัด | " + " | ".join(engines) + " |", "|---|" + "---|" * len(engines)]
    for k, label in cols:
        md.append(f"| {label} | " + " | ".join(str(summary[e][k]) for e in engines) + " |")
    md += ["", "รายไฟล์: per_file.csv · ข้อความดิบ/หลังแก้: <engine>/<sha>.raw.txt / .txt"]
    (OUT / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="tesseract,akson,openthai")
    ap.add_argument("--limit", type=int, help="จำนวนไฟล์ (เรียงตามเลขทะเบียน)")
    ap.add_argument("--force", action="store_true", help="OCR ใหม่แม้มีผลแล้ว")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--dir", help="OCR ไฟล์ PDF ทั้งหมดในโฟลเดอร์นี้แทนไฟล์ของทะเบียน")
    ap.add_argument("--extra-sha", default="", help="sha256 ของไฟล์ในคลังที่ต้องการเพิ่ม คั่นด้วย ,")
    a = ap.parse_args()
    engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    if not a.report_only:
        run(engines, a.limit, a.force, a.dir, [x.strip() for x in a.extra_sha.split(",") if x.strip()])
    ready = [e for e in engines if (OUT / e).exists() and any((OUT / e).glob("*.json"))]
    if ready:
        report(ready)


if __name__ == "__main__":
    main()
