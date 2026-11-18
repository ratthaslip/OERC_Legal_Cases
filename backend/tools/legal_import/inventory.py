"""บัญชีไฟล์กฎหมายลำดับรองจากฝ่ายกฎหมาย (Google Drive zip) ↔ ทะเบียน

การจับคู่ตามโครงสร้างโฟลเดอร์:
  01.1-01.52/1.12.1/...      → ทะเบียน 1.12.1 (กลุ่ม 1)
  23/ไฟล์                     → แถวแรกของกลุ่ม 23 (ฉบับหลัก)
  23/2. ชื่อ/ไฟล์             → แถวที่ 2 ถัดจากฉบับหลักของกลุ่ม 23 (ตามลำดับในทะเบียน)
ทะเบียนบางแถวเลขหาย 0 ท้าย (1.10 → 1.1) จากการเก็บเป็นตัวเลขใน Excel — แก้ตามลำดับแถว
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import psycopg

import os

WORK = Path(os.environ.get("LEGAL_IMPORT_WORK", "work"))          # โฟลเดอร์ทำงาน (นอก git — มีข้อมูลทะเบียน)
DSN = os.environ.get("LEGAL_IMPORT_DSN", "host=localhost port=5432 user=postgres password=postgres dbname=erc")
DIRS = [Path(p) for p in sys.argv[1:]]                               # โฟลเดอร์ที่ได้จาก extract_zip.py
OUT = WORK / "inventory.json"
TXT = WORK / "text"
TXT.mkdir(parents=True, exist_ok=True)
THAI = re.compile(r"[฀-๿]")


def extract(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True, text=True, timeout=120).stdout
        if ext == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
        if ext == ".doc":
            out = TXT / "doc"
            out.mkdir(exist_ok=True)
            subprocess.run(["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", str(out), str(path)],
                           capture_output=True, timeout=180)
            f = out / (path.stem + ".txt")
            return f.read_text(encoding="utf-8", errors="ignore") if f.exists() else ""
    except Exception as e:  # noqa: BLE001
        return f"__ERROR__ {e}"
    return ""


def register_rows() -> list[dict]:
    with psycopg.connect(DSN) as c:
        cur = c.execute("""SELECT register_id, sheet_row, reg_no, group_no, doc_type, status, title_th, effective_date,
                                  authority_secs, instrument_eli
                           FROM law_register WHERE status <> 'GROUP' ORDER BY sheet_row""")
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    # แก้เลขที่หาย 0 ท้ายในกลุ่ม 1 (1.1 หลัง 1.9.2 = 1.10)
    prev = 0
    for r in rows:
        r["reg_key"] = r["reg_no"]
        if r["group_no"] == "1":
            parts = r["reg_no"].split(".")
            m = int(parts[1])
            if m < prev and m * 10 in (prev, prev + 1):
                m *= 10
            parts[1] = str(m)
            r["reg_key"] = ".".join(parts)
            prev = max(prev, m)
    return rows


THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def _year(s: str) -> str | None:
    s = s.translate(THAI_DIGITS)
    m = re.findall(r"25(\d\d)|(?<!\d)(\d\d)(?!\d)", s)
    ys = [a or b for a, b in m if (a or b) and 40 <= int(a or b) <= 70]
    return ys[-1] if ys else None


def _edition(s: str) -> int:
    s = s.translate(THAI_DIGITS)
    m = re.search(r"ฉ(?:บับ(?:ที่)?)?\.?\s*(\d+)", s)
    return int(m.group(1)) if m else 1


def guess_by_title(grp: list[dict], name: str, inv: dict) -> dict | None:
    """สำรอง: ปี พ.ศ. + ฉบับที่ ในชื่อไฟล์ ตรงกับชื่อเรื่องในทะเบียน และแถวนั้นยังไม่มีไฟล์"""
    y, e = _year(name), _edition(name)
    cands = [r for r in grp if _year(r["title_th"]) == y and _edition(r["title_th"]) == e]
    empty = [r for r in cands if not inv[r["register_id"]]["files"]]
    return (empty or cands or [None])[0] if len(cands) >= 1 else None


def main() -> None:
    rows = register_rows()
    by_key = {r["reg_key"]: r for r in rows if r["group_no"] == "1"}
    by_group: dict[str, list[dict]] = {}
    for r in rows:
        by_group.setdefault(r["group_no"], []).append(r)

    files = []
    for d in DIRS:
        for x in json.loads((d / "manifest.json").read_text(encoding="utf-8")):
            x["disk"] = str(d / "files" / f"{x['sha256']}{x['ext']}")
            files.append(x)
    extracted: dict[str, dict] = {}
    # กลุ่มที่ไม่มีไฟล์ฉบับหลัก (ทุกไฟล์มีเลขนำ "1." "2." …) → เลข k = แถวที่ k ของกลุ่ม (นับ 1 จากแถวแรก)
    numbered_only = {}
    for x in files:
        parts = x["folder"].split("/")
        if parts[0].isdigit():
            first = parts[1] if len(parts) > 1 else x["name"]
            numbered_only.setdefault(parts[0], []).append(bool(re.match(r"\d+\s*\.", first)))
    numbered_only = {g for g, v in numbered_only.items() if all(v)}
    inv = {r["register_id"]: {**{k: (str(v) if k == "effective_date" and v else v) for k, v in r.items()}, "files": []}
           for r in rows}
    unmatched = []
    for x in files:
        parts = x["folder"].split("/")
        row = None
        if parts[0].startswith("01."):
            row = by_key.get(parts[1]) if len(parts) > 1 else None
        elif parts[0].isdigit() and len(parts) > 1 and re.fullmatch(r"\d+(\.\d+)+", parts[1]):
            # โฟลเดอร์ย่อยตั้งชื่อตามเลขทะเบียน (163/163.7/…) — เลขซ้ำหลายแถว เลือกตามเลขนำหน้าไฟล์ (01, 02)
            same = [r for r in by_group.get(str(int(parts[0])), []) if r["reg_no"] == parts[1]]
            m = re.match(r"0*(\d+)\s", x["name"])
            k = int(m.group(1)) - 1 if m and len(same) > 1 else 0
            row = same[k] if 0 <= k < len(same) else (same[0] if same else None)
        elif parts[0].isdigit():
            grp = by_group.get(str(int(parts[0])), [])
            first = parts[1] if len(parts) > 1 else x["name"]
            m = re.match(r"(\d+)\s*\.", first)
            k = int(m.group(1)) if m else 0
            if parts[0] in numbered_only:
                k -= 1
            row = grp[k] if 0 <= k < len(grp) else None
        sha = x["sha256"]
        if sha in extracted:
            text = extracted[sha]["text"]
        else:
            text = extract(Path(x["disk"]))
            extracted[sha] = {"text": text}
            (TXT / f"{sha}.txt").write_text(text, encoding="utf-8")
        info = {"name": x["name"], "path": x["path"], "ext": x["ext"], "size": x["size"], "sha256": sha,
                "disk": x["disk"], "thai_chars": len(THAI.findall(text)),
                "pages": None}
        if x["ext"] == ".pdf":
            pi = subprocess.run(["pdfinfo", x["disk"]], capture_output=True, text=True).stdout
            m = re.search(r"Pages:\s+(\d+)", pi)
            info["pages"] = int(m.group(1)) if m else None
        info["scanned"] = x["ext"] == ".pdf" and info["thai_chars"] < 150 * max(1, (info["pages"] or 1) // 2)
        if not row and parts[0].isdigit():
            row = guess_by_title(by_group.get(str(int(parts[0])), []), x["name"], inv)
            if row:
                info["matched_by"] = "title"
        if row:
            inv[row["register_id"]]["files"].append(info)
        else:
            unmatched.append(info)
    covered_groups = {str(int(f["path"].split("/")[0])) if f["path"].split("/")[0].isdigit() else "1" for f in files}
    OUT.write_text(json.dumps({"rows": list(inv.values()), "unmatched": unmatched,
                               "covered_groups": sorted(covered_groups, key=int)},
                              ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("files", len(files), "unique", len(extracted), "unmatched", len(unmatched))


if __name__ == "__main__":
    main()
