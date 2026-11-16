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
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_engine
from ..law.register import read_workbook
from ..search.indexer import reindex_register


def load_register(conn: Connection, path: str) -> dict:
    items, sheet_title = read_workbook(path)
    conn.execute(text("DELETE FROM law_register"))
    for r in items:
        conn.execute(text("""
            INSERT INTO law_register (sheet_row, reg_no, group_no, doc_type, title_th, authority_text, authority_secs,
                owner_unit, effective_text, effective_date, review_text, status, status_text, note, hearing_text,
                attachments, issues, source_file)
            VALUES (:sheet_row, :reg_no, :group_no, :doc_type, :title_th, :authority_text, :authority_secs,
                :owner_unit, :effective_text, :effective_date, :review_text, :status, :status_text, :note,
                :hearing_text, CAST(:attachments AS jsonb), :issues, :source_file)"""),
            {**r.__dict__, "attachments": json.dumps(r.attachments), "source_file": Path(path).name})
    link_instruments(conn)
    n = reindex_register(conn)
    return {"sheet": sheet_title, "rows": len(items), "indexed": n, "items": items}


def _title_key(t: str) -> str:
    """ชื่อทางการกับชื่อในทะเบียนเขียนต่างกัน: 'คณะกรรมการกำกับกิจการพลังงาน' ↔ 'กกพ.', เลขไทย/อารบิก, ช่องว่าง"""
    t = t.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")).replace("\u0e4d\u0e32", "\u0e33")
    t = t.replace("คณะกรรมการกำกับกิจการพลังงาน", "กกพ.").replace("กกพ", "กกพ.").replace("..", ".")
    return re.sub(r"[\s.]", "", t)


def link_instruments(conn: Connection) -> int:
    """ผูกระเบียนทะเบียนกับตัวบทเต็มที่นำเข้าแล้ว (ตัวบทจริงเท่านั้น) โดยเทียบชื่อหลังปรับรูปแบบ"""
    insts = {_title_key(r[1]): r[0] for r in conn.execute(
        text("SELECT eli_id, title_th FROM legal_instrument WHERE is_demo = FALSE"))}
    n = 0
    for rid, title in conn.execute(text("SELECT register_id, title_th FROM law_register")).all():
        eli = insts.get(_title_key(title))
        if eli:
            conn.execute(text("UPDATE law_register SET instrument_eli = :e WHERE register_id = :i"), {"e": eli, "i": rid})
            n += 1
    return n


def summarize(items) -> str:
    st = collections.Counter(r.status for r in items)
    iss = collections.Counter(i.split(" (")[0].split(" —")[0] for r in items for i in r.issues)
    lines = [f"ระเบียน {len(items)} แถว · เรื่องหลัก {len({r.group_no for r in items})} กลุ่ม",
             f"สถานะ: ใช้บังคับ {st['IN_FORCE']} · ยกเลิก {st['REPEALED']} · ไม่ระบุ {st['UNKNOWN']}",
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
