"""นำเข้าทะเบียนคุมกฎหมายลำดับรอง (Excel) เข้า law_register แล้ว index ใหม่

ใช้งาน:
    python -m app.seed.import_register /data/register.xlsx [--report /data/register-issues.csv] [--dry-run]

- แทนที่ระเบียนเดิมทั้งหมดทุกครั้ง (ทะเบียน Excel คือแหล่งความจริง แก้ที่ Excel แล้วนำเข้าใหม่)
- ไม่นำเข้าคอลัมน์ "นิติกรผู้รับผิดชอบ"
- --report เขียนรายการแถวที่มีปัญหาคุณภาพข้อมูลเป็น CSV (UTF-8 BOM เปิดด้วย Excel ได้) ส่งให้ฝ่ายกฎหมายแก้
- ไฟล์ Excel และ report เป็นข้อมูลจริงของสำนักงาน: เก็บใน ./data (อยู่ใน .gitignore) ห้าม commit
"""
import collections
import csv
import hashlib
import os
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_engine
from ..law.register import read_workbook
from ..config import public_data_only
from ..law import relations
from ..search.indexer import reindex_register


# คอลัมน์ที่ระบบแสดงเฉพาะเจ้าหน้าที่ (law/router.py _INTERNAL_COLS) — เครื่อง PUBLIC_DATA_ONLY ไม่เก็บ
INTERNAL_FIELDS = {"owner_unit": None, "review_text": None, "status_text": None, "note": None, "hearing_text": None,
                   "attachments": {}, "issues": []}
PUBLIC_FIELDS = ("sheet_row", "reg_no", "group_no", "doc_type", "title_th", "authority_text", "authority_secs",
                 "effective_text", "effective_date", "status", "status_assumed")


def load_register(conn: Connection, path: str) -> dict:
    items, sheet_title = read_workbook(path)
    if public_data_only():
        for r in items:
            for k, v in INTERNAL_FIELDS.items():
                setattr(r, k, v)
    conn.execute(text("DELETE FROM law_register"))
    for r in items:
        conn.execute(text("""
            INSERT INTO law_register (sheet_row, reg_no, group_no, doc_type, title_th, authority_text, authority_secs,
                owner_unit, effective_text, effective_date, review_text, status, status_text, status_assumed, note, hearing_text,
                attachments, issues, source_file)
            VALUES (:sheet_row, :reg_no, :group_no, :doc_type, :title_th, :authority_text, :authority_secs,
                :owner_unit, :effective_text, :effective_date, :review_text, :status, :status_text, :status_assumed, :note,
                :hearing_text, CAST(:attachments AS jsonb), :issues, :source_file)"""),
            {**r.__dict__, "attachments": json.dumps(r.attachments), "source_file": Path(path).name})
    linked = link_instruments(conn)
    relations.rebuild(conn)  # เส้น authorizes มาจากทะเบียน
    archived = None if public_data_only() else archive_source(conn, Path(path))
    n = reindex_register(conn)
    conn.execute(text("""
        INSERT INTO law_import_log (kind, source_name, source_sha256, rows_total, rows_linked, actor, detail)
        VALUES ('register', :src, :sha, :rows, :linked, :actor, CAST(:detail AS jsonb))"""), {
        "src": Path(path).name, "sha": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "rows": sum(1 for r in items if r.status != "GROUP"), "linked": linked,
        "actor": os.environ.get("IMPORT_ACTOR") or "seed",
        "detail": json.dumps({"sheet": sheet_title, "with_issues": sum(1 for r in items if r.issues),
                              "archived": archived}, ensure_ascii=False)})
    return {"sheet": sheet_title, "rows": len(items), "indexed": n, "items": items}


def load_register_json(conn: Connection, path: Path) -> dict:
    """ทะเบียนฉบับเฉพาะคอลัมน์สาธารณะ (จาก public_only export-register) — ใช้บนเครื่องที่ไม่เก็บไฟล์ทะเบียนต้นฉบับ"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    conn.execute(text("DELETE FROM law_register"))
    for r in data["rows"]:
        conn.execute(text("""
            INSERT INTO law_register (sheet_row, reg_no, group_no, doc_type, title_th, authority_text, authority_secs,
                effective_text, effective_date, status, status_assumed, attachments, issues, source_file)
            VALUES (:sheet_row, :reg_no, :group_no, :doc_type, :title_th, :authority_text, :authority_secs,
                :effective_text, CAST(:effective_date AS date), :status, :status_assumed, '{}'::jsonb, '{}', :src)"""),
            {**{k: r.get(k) for k in PUBLIC_FIELDS}, "src": Path(path).name})
    linked = link_instruments(conn)
    relations.rebuild(conn)
    n = reindex_register(conn)
    return {"sheet": data.get("sheet"), "rows": len(data["rows"]), "indexed": n, "linked": linked}


def export_register_json(conn: Connection, path: Path) -> int:
    """เขียนทะเบียนจากฐานข้อมูลเฉพาะคอลัมน์ที่ประชาชนเห็น (ไม่มีหน่วยงาน หมายเหตุ การทบทวน ไฟล์แนบ ข้อสังเกต)"""
    cols = ", ".join(PUBLIC_FIELDS)
    rs = [dict(r._mapping) for r in conn.execute(text(f"SELECT {cols} FROM law_register ORDER BY sheet_row"))]
    for r in rs:
        r["effective_date"] = r["effective_date"].isoformat() if r["effective_date"] else None
    sheet = conn.execute(text("SELECT detail->>'sheet' FROM law_import_log WHERE kind = 'register' "
                              "ORDER BY 1 DESC LIMIT 1")).scalar()
    Path(path).write_text(json.dumps({"sheet": sheet, "rows": rs}, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rs)


def archive_source(conn: Connection, path: Path) -> str | None:
    """เก็บสำเนาไฟล์ทะเบียนที่นำเข้าไว้ในที่เก็บไฟล์ต้นฉบับ (internal) — ย้อนดูได้ว่านำเข้าจากไฟล์รุ่นไหน"""
    from .raw_files import add_file
    try:
        res = add_file(conn, path, "internal", source="ฝ่ายกฎหมาย สำนักงาน กกพ. (ทะเบียนกฎหมายลำดับรอง)",
                       received_from=os.environ.get("IMPORT_ACTOR") or None)
        return res["stored"]
    except OSError as e:  # ที่เก็บไฟล์ยังไม่ได้เมานต์/เขียนไม่ได้ — นำเข้าทะเบียนต่อได้ แต่บันทึกไว้
        print(f"คำเตือน: เก็บสำเนาทะเบียนไม่สำเร็จ ({e})")
        return None


def _title_key(t: str) -> str:
    """ชื่อทางการกับชื่อในทะเบียนเขียนต่างกัน: 'คณะกรรมการกำกับกิจการพลังงาน' ↔ 'กกพ.', เลขไทย/อารบิก, ช่องว่าง"""
    t = t.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")).replace("\u0e4d\u0e32", "\u0e33")
    t = t.replace("คณะกรรมการกำกับกิจการพลังงาน", "กกพ.").replace("กกพ", "กกพ.").replace("..", ".")
    # ชื่อในทะเบียนบางแถวคัดลอกจาก PDF แล้วสระ/วรรณยุกต์หาย ("วาดวย") → ตัดเครื่องหมายบน/ล่างออกทั้งสองฝั่งก่อนเทียบ
    t = re.sub("[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]", "", t)
    return re.sub(r"[\s.]", "", t)


def link_instruments(conn: Connection) -> int:
    """ผูกระเบียนทะเบียนกับตัวบทเต็มที่นำเข้าแล้ว (ตัวบทจริงเท่านั้น) โดยเทียบชื่อหลังปรับรูปแบบ"""
    insts = {}
    for eli, title, eff, reg_title in conn.execute(text(
            "SELECT eli_id, title_th, effective_date, register_title FROM legal_instrument WHERE is_demo = FALSE")):
        insts.setdefault(_title_key(title), (eli, eff))
        if reg_title:  # ไฟล์จากฝ่ายกฎหมายระบุชื่อตามทะเบียนไว้ — ผูกได้แม้ชื่อทางการเขียนต่างจากทะเบียน
            insts[_title_key(reg_title)] = (eli, eff)
    n = 0
    for rid, title, eff in conn.execute(text("SELECT register_id, title_th, effective_date FROM law_register")).all():
        hit = insts.get(_title_key(title))
        if not hit:
            continue
        eli, inst_eff = hit
        conn.execute(text("UPDATE law_register SET instrument_eli = :e WHERE register_id = :i"), {"e": eli, "i": rid})
        # ตัวบทที่นำเข้าแล้วสิ้นผลทุกข้อ (ถูกยกเลิก) → แก้สถานะที่สันนิษฐานไว้
        alive = conn.execute(text("""SELECT count(*) FROM provision_version pv JOIN provision p USING (provision_id)
                                     JOIN legal_instrument i USING (instrument_id)
                                     WHERE i.eli_id = :e AND pv.valid_to IS NULL"""), {"e": eli}).scalar()
        if alive == 0:
            conn.execute(text("""UPDATE law_register SET status = 'REPEALED', status_assumed = FALSE
                                 WHERE register_id = :i AND status_assumed"""), {"i": rid})
        if inst_eff and eff != inst_eff:  # ตัวบทคือแหล่งความจริง: แจ้งให้แก้ทะเบียน
            th = f"{inst_eff.day}/{inst_eff.month}/{inst_eff.year + 543}"
            conn.execute(text("UPDATE law_register SET issues = array_append(issues, :m) WHERE register_id = :i"),
                         {"m": f"วันใช้บังคับในทะเบียนไม่ตรงกับตัวบท (ตามตัวบทคือ {th})", "i": rid})
        n += 1
    return n


def summarize(items) -> str:
    st = collections.Counter(r.status for r in items)
    iss = collections.Counter(i.split(" (")[0].split(" —")[0] for r in items for i in r.issues)
    lines = [f"ระเบียน {len(items)} แถว · เรื่องหลัก {len({r.group_no for r in items})} กลุ่ม",
             f"สถานะ: ใช้บังคับ {st['IN_FORCE']} (ในจำนวนนี้สันนิษฐาน {sum(1 for r in items if r.status_assumed)})"
             f" · ยกเลิก {st['REPEALED']} · ไม่ระบุ {st['UNKNOWN']}",
             f"แถวที่มีประเด็นต้องตรวจ {sum(1 for r in items if r.issues)}"]
    lines += [f"  - {k}: {v}" for k, v in iss.most_common()]
    return "\n".join(lines)


def write_report(items, out: str) -> None:
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["แถวใน Excel", "ลำดับ", "ประเภท", "รายชื่อกฎหมาย", "ประเด็นที่ต้องตรวจ"])
        for r in items:
            if r.issues:
                w.writerow([r.sheet_row, r.reg_no, r.doc_type or "", r.title_th, " | ".join(r.issues)])


def main(argv: list[str]) -> None:
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__)
    path = argv[0]
    report = argv[argv.index("--report") + 1] if "--report" in argv else None
    if "--dry-run" in argv:
        items, title = read_workbook(path)
        print(title)
        print(summarize(items))
    else:
        with get_engine().begin() as conn:
            res = load_register(conn, path)
        items = res["items"]
        print(res["sheet"])
        print(summarize(items))
        print(f"index สำหรับค้นหา/ผู้ช่วย AI {res['indexed']} รายการ")
    if report:
        write_report(items, report)
        print(f"เขียนรายงานประเด็นข้อมูลที่ {report}")


if __name__ == "__main__":
    main(sys.argv[1:])
