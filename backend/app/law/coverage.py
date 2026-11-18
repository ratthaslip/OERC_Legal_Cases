"""ความครอบคลุมของข้อมูลกฎหมาย: นำเข้าตัวบทแล้วกี่ฉบับ ทะเบียนมีตัวบทเต็มกี่ฉบับ ยังค้างอะไร นำเข้าเมื่อไร

ใช้ทั้งหน้า "ความครอบคลุมข้อมูล" และไฟล์ส่งออกรายการที่ยังไม่มีตัวบท (ส่งให้ฝ่ายกฎหมายเพื่อขอไฟล์)
"""
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.engine import Connection

from ..db import one, rows
from . import relations

TYPE_TH = {"act": "พระราชบัญญัติ", "regulation": "ระเบียบ", "announcement": "ประกาศ"}
STATUS_TH = {"IN_FORCE": "ใช้บังคับ", "REPEALED": "ยกเลิก", "UNKNOWN": "ไม่ระบุ"}
ISSUE_TH = {"date_estimate": "วันใช้บังคับเป็นค่าประมาณ", "pending_amendment": "ฉบับแก้ไขที่ยังไม่ได้รวม",
            "placeholder_text": "ถ้อยคำยังไม่ครบ", "needs_confirmation": "ต้องยืนยันความเป็นปัจจุบัน",
            "verify_consolidation": "ขอตรวจการประกอบตัวบท", "import_note": "ข้อสังเกตจากการแปลงไฟล์"}

_HAS_TEXT = "(instrument_eli IS NOT NULL)"


def summary(conn: Connection) -> dict:
    instruments = rows(conn, """
        SELECT i.eli_id, i.title_th, i.instrument_type, i.effective_date,
               count(DISTINCT p.provision_id) AS provisions, count(pv.version_id) AS versions,
               count(DISTINCT p.provision_id) FILTER (WHERE pv.version_no > 1) AS amended_provisions,
               (SELECT reg_no FROM law_register r WHERE r.instrument_eli = i.eli_id AND r.status <> 'GROUP'
                 ORDER BY sheet_row LIMIT 1) AS register_no,
               (SELECT row_to_json(l) FROM (SELECT source_name, left(source_sha256, 12) AS sha, actor, imported_at
                  FROM law_import_log WHERE instrument_eli = i.eli_id ORDER BY imported_at DESC LIMIT 1) l) AS last_import,
               (SELECT count(*) FROM law_open_issue o WHERE o.instrument_eli = i.eli_id) AS open_issues,
               (SELECT count(*) FROM raw_file_link l WHERE l.instrument_eli = i.eli_id) AS source_links,
               (SELECT count(*) FROM raw_file_link l JOIN raw_file f USING (sha256)
                 WHERE l.instrument_eli = i.eli_id) AS source_available,
               (SELECT p2.eli_id FROM provision p2 WHERE p2.instrument_id = i.instrument_id
                 ORDER BY p2.sort_order LIMIT 1) AS first_provision_eli
        FROM legal_instrument i
        LEFT JOIN provision p USING (instrument_id)
        LEFT JOIN provision_version pv ON pv.provision_id = p.provision_id AND pv.status = 'approved'
        WHERE i.is_demo = FALSE
        GROUP BY i.instrument_id ORDER BY i.instrument_type, i.effective_date""")
    by_type: dict[str, int] = {}
    for i in instruments:
        by_type[i["instrument_type"]] = by_type.get(i["instrument_type"], 0) + 1
        i["type_th"] = TYPE_TH.get(i["instrument_type"], i["instrument_type"])
    demo = one(conn, "SELECT count(*) AS n FROM legal_instrument WHERE is_demo")["n"]

    base = "FROM law_register WHERE status <> 'GROUP'"
    reg = one(conn, f"""SELECT count(*) AS total, count(*) FILTER (WHERE {_HAS_TEXT}) AS with_text,
                               count(*) FILTER (WHERE coalesce((attachments->>'pdf')::int, 0) > 0
                                                  OR coalesce((attachments->>'word')::int, 0) > 0) AS with_files,
                               count(*) FILTER (WHERE coalesce((attachments->>'word')::int, 0) > 0) AS with_word,
                               count(*) FILTER (WHERE cardinality(issues) > 0) AS with_issues,
                               count(*) FILTER (WHERE status = 'IN_FORCE' AND NOT {_HAS_TEXT}) AS in_force_missing,
                               max(source_file) AS source_file, max(imported_at) AS imported_at {base}""")
    reg["by_status"] = rows(conn, f"""SELECT status AS key, count(*) AS total,
                                             count(*) FILTER (WHERE {_HAS_TEXT}) AS with_text,
                                             count(*) FILTER (WHERE status_assumed) AS assumed
                                      {base} GROUP BY 1 ORDER BY 2 DESC""")
    for s in reg["by_status"]:
        s["label"] = STATUS_TH.get(s["key"], s["key"])
    reg["by_group"] = rows(conn, f"""
        SELECT r.group_no AS key, coalesce(g.title_th, (array_agg(r.title_th ORDER BY r.sheet_row))[1]) AS label, count(*) AS total, count(*) FILTER (WHERE r.instrument_eli IS NOT NULL) AS with_text
        FROM law_register r LEFT JOIN law_register g ON g.group_no = r.group_no AND g.status = 'GROUP'
        WHERE r.status <> 'GROUP' GROUP BY r.group_no, g.title_th
        ORDER BY r.group_no ~ '^[0-9]+$' DESC, NULLIF(regexp_replace(r.group_no, '[^0-9]', '', 'g'), '')::int""")
    reg["by_section"] = rows(conn, f"""SELECT s AS key, count(*) AS total, count(*) FILTER (WHERE {_HAS_TEXT}) AS with_text
                                       FROM law_register, unnest(authority_secs) s WHERE status <> 'GROUP'
                                       GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 15""")
    reg["by_doc_type"] = rows(conn, f"""SELECT coalesce(doc_type, 'ไม่ระบุ') AS key, count(*) AS total,
                                               count(*) FILTER (WHERE {_HAS_TEXT}) AS with_text
                                        {base} GROUP BY 1 ORDER BY 2 DESC""")

    issues = rows(conn, """SELECT o.issue_id, o.instrument_eli, o.provision_eli, o.kind, o.note, i.title_th,
                                  coalesce(o.provision_eli, (SELECT p.eli_id FROM provision p
                                     WHERE p.instrument_id = i.instrument_id ORDER BY p.sort_order LIMIT 1)) AS open_eli
                           FROM law_open_issue o LEFT JOIN legal_instrument i ON i.eli_id = o.instrument_eli
                           ORDER BY o.kind, o.issue_id""")
    for x in issues:
        x["kind_th"] = ISSUE_TH.get(x["kind"], x["kind"])
    imports = rows(conn, """SELECT import_id, kind, source_name, left(source_sha256, 12) AS sha, instrument_eli, title_th,
                                   provisions, versions, rows_total, rows_linked, actor, imported_at
                            FROM law_import_log ORDER BY imported_at DESC, import_id DESC LIMIT 30""")
    raw = {
        "by_class": rows(conn, """SELECT classification AS key, count(*) AS files, coalesce(sum(size_bytes), 0) AS bytes
                                  FROM raw_file GROUP BY 1 ORDER BY 1"""),
        "instruments_with_source": sum(1 for i in instruments if i["source_available"] > 0),
        "missing": rows(conn, """SELECT l.sha256, l.instrument_eli FROM raw_file_link l
                                 LEFT JOIN raw_file f USING (sha256) WHERE f.sha256 IS NULL ORDER BY 2"""),
        "unlinked_public": one(conn, """SELECT count(*) AS n FROM raw_file f WHERE classification = 'public'
                                        AND NOT EXISTS (SELECT 1 FROM raw_file_link l WHERE l.sha256 = f.sha256)""")["n"],
    }
    return {
        "raw": raw,
        "laws": {"instruments": len(instruments), "by_type": by_type, "demo_instruments": demo,
                 "provisions": sum(i["provisions"] for i in instruments),
                 "versions": sum(i["versions"] for i in instruments),
                 "amended_provisions": sum(i["amended_provisions"] for i in instruments)},
        "instruments": instruments, "register": reg, "open_issues": issues, "imports": imports,
        "last_import": imports[0]["imported_at"] if imports else None,
        "relations": relations.stats(conn),
    }


def missing_workbook(conn: Connection, where_sql: str, params: dict) -> bytes:
    """รายการทะเบียนตามตัวกรอง → Excel สำหรับฝ่ายกฎหมาย (มีช่องให้กรอกชื่อไฟล์ที่ส่งมา)"""
    items = rows(conn, f"""
        SELECT r.reg_no, r.doc_type, r.title_th, r.authority_text, r.effective_text, r.effective_date, r.status,
               r.status_assumed, g.title_th AS group_title, r.attachments, r.instrument_eli, r.sheet_row
        FROM law_register r LEFT JOIN law_register g ON g.group_no = r.group_no AND g.status = 'GROUP'
        WHERE {where_sql}
        ORDER BY r.group_no ~ '^[0-9]+$' DESC, NULLIF(regexp_replace(r.group_no, '[^0-9]', '', 'g'), '')::int, r.sheet_row""",
                 **params)
    wb = Workbook()
    ws = wb.active
    ws.title = "รายการ"
    font, bold = Font(name="TH Sarabun New", size=14), Font(name="TH Sarabun New", size=14, bold=True, color="FFFFFF")
    head = ["เลขทะเบียน", "กลุ่ม", "ประเภท", "ชื่อ", "บทอาศัยอำนาจ (มาตรา)", "วันใช้บังคับ", "สถานะ",
            "ไฟล์ในทะเบียน (PDF/Word)", "ตัวบทในระบบ", "แถวใน Excel ทะเบียน", "ชื่อไฟล์ที่ส่งมา", "หมายเหตุ"]
    widths = [11, 30, 14, 70, 14, 16, 12, 16, 12, 10, 26, 30]
    for c, (h, w) in enumerate(zip(head, widths), 1):
        cell = ws.cell(1, c, h)
        cell.font, cell.fill = bold, PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[cell.column_letter].width = w
    fill = PatternFill("solid", fgColor="FFF2CC")
    for r, i in enumerate(items, 2):
        att = i["attachments"] or {}
        files = f"{att.get('pdf', 0)}/{att.get('word', 0)}"
        status = STATUS_TH.get(i["status"], i["status"]) + ("*" if i["status_assumed"] else "")
        eff = i["effective_date"].isoformat() if isinstance(i["effective_date"], date) else (i["effective_text"] or "")
        vals = [i["reg_no"], i["group_title"], i["doc_type"], i["title_th"], i["authority_text"], eff, status, files,
                "มี" if i["instrument_eli"] else "ยังไม่มี", i["sheet_row"], None, None]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v)
            cell.font, cell.alignment = font, Alignment(wrap_text=True, vertical="top")
            if c >= 11:
                cell.fill = fill
    ws.freeze_panes = "E2"
    ws.auto_filter.ref = f"A1:L{len(items) + 1}"
    note = wb.create_sheet("หมายเหตุ")
    for r, t in enumerate([
        "รายการนี้ส่งออกจากระบบสารสนเทศกฎหมาย สำนักงาน กกพ. เพื่อขอไฟล์ตัวบทกฎหมายลำดับรองที่ยังไม่มีในระบบ",
        "ตั้งชื่อไฟล์ตามเลขทะเบียน เช่น 1.52.3.docx — ไฟล์ Word ดีที่สุด (ดึงข้อความได้แม่นกว่า PDF)",
        "ส่งเฉพาะตัวบทกฎหมาย ไม่รวมเอกสารภายใน (มติ กกพ., บันทึกตอบข้อหารือ)",
        "สถานะที่มี * = ทะเบียนไม่ระบุ ระบบถือว่าใช้บังคับไว้ก่อน",
        f"ส่งออกเมื่อ {date.today().isoformat()} · {len(items)} รายการ",
    ], 1):
        note.cell(r, 1, t).font = font
    note.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
