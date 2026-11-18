"""อ่านทะเบียนคุมกฎหมายลำดับรอง (Excel ของฝ่ายกฎหมาย) → ระเบียน law_register

ฟังก์ชันในไฟล์นี้เป็น pure logic (ไม่แตะฐานข้อมูล) เพื่อทดสอบได้ง่าย
หลักการ: ไม่แก้ถ้อยคำกฎหมายเอง — เก็บค่าตามต้นฉบับ แล้วบันทึก "issues" ให้ฝ่ายกฎหมายแก้ที่ Excel
ไม่นำเข้าคอลัมน์ "นิติกรผู้รับผิดชอบ" (ข้อมูลส่วนบุคคล ไม่จำเป็นต่อการสืบค้น)
"""
from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# ทะเบียนไม่ระบุสถานะ → ถือว่า "ใช้บังคับ" ไว้ก่อน (ตัดสินใจของผู้ดูแลโครงการ 16 พ.ย. 2569) และทำเครื่องหมายว่าสันนิษฐาน
# ตั้ง REGISTER_ASSUME_IN_FORCE=0 เพื่อกลับไปแสดงเป็น "ไม่ระบุ"
ASSUME_IN_FORCE = os.environ.get("REGISTER_ASSUME_IN_FORCE", "1") != "0"

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
MONTHS = {m: i + 1 for i, m in enumerate(
    "มกราคม กุมภาพันธ์ มีนาคม เมษายน พฤษภาคม มิถุนายน กรกฎาคม สิงหาคม กันยายน ตุลาคม พฤศจิกายน ธันวาคม".split())}
MONTHS_SHORT = {m: i + 1 for i, m in enumerate("ม.ค. ก.พ. มี.ค. เม.ย. พ.ค. มิ.ย. ก.ค. ส.ค. ก.ย. ต.ค. พ.ย. ธ.ค.".split())}

# หัวคอลัมน์ที่ต้องมี (เทียบแบบตัดช่องว่าง/ขึ้นบรรทัด) → ชื่อฟิลด์
HEADERS = {
    "ลำดับ": "reg_no", "ประเภท": "doc_type", "รายชื่อกฎหมาย": "title", "บทอาศัยอำนาจ": "authority",
    "ฝ่ายงานผู้รับผิดชอบ": "unit", "วันใช้บังคับ": "effective", "รอบการประเมินผลสัมฤทธิ์(ครบ5ปี)": "review",
    "รับฟังหลักการ": "hear_principle", "รับฟังร่างกฎหมาย": "hear_draft", "สถานะปัจจุบัน": "status",
    "หมายเหตุ": "note",
}
STAT_HEADERS = {
    "กฎหมายPDF": "pdf", "กฎหมายWORD": "word", "มติกกพ.": "erc_resolution", "มติอนุกม.": "legal_subc_resolution",
    "มติอนุกรรมการที่เกี่ยวข้อง": "subc_resolution", "การตอบข้อหารือภายใน": "internal_opinion",
    "การหารือหน่วยงานภายนอก": "external_consult", "เอกสารอื่นๆ": "other_docs",
}
# ร่องรอยการคัดลอกจาก PDF ที่สระ/วรรณยุกต์หาย (ตรวจเท่านั้น ไม่แก้ให้)
GARBLED = re.compile(r"ก ากับ|กากับ|กํากับ|วาดวย|แหง|หลักเกณฑ |คาใช|หนาที่|พ\.ศ\.\s*[๐-๙0-9]{3}(?![๐-๙0-9])")


def _key(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


# ข้อความที่คัดลอกจาก PDF ฟอนต์ไทยรุ่นเก่า มีสระ/วรรณยุกต์ใน Private Use Area (มองไม่เห็นในหลายโปรแกรม)
PUA = str.maketrans({0xF700: 'ฐ', 0xF701: 'ิ', 0xF702: 'ี', 0xF703: 'ึ', 0xF704: 'ื', 0xF705: '่', 0xF706: '้',
                     0xF707: '๊', 0xF708: '๋', 0xF709: '์', 0xF70A: '่', 0xF70B: '้', 0xF70C: '๊', 0xF70D: '๋',
                     0xF70E: '์', 0xF70F: 'ญ', 0xF710: 'ั', 0xF711: 'ํ', 0xF712: '็', 0xF713: '่', 0xF714: '้',
                     0xF715: '๊', 0xF716: '๋', 0xF717: '์', 0xF718: 'ุ', 0xF719: 'ู', 0xF71A: 'ฺ'})


def clean(s) -> str | None:
    if s is None:
        return None
    t = re.sub(r"\s+", " ", str(s).translate(PUA)).strip().replace("\u0e4d\u0e32", "\u0e33")
    return t or None


def parse_thai_date(s) -> date | None:
    """'6 มกราคม 2566', '๑๒ มีนาคม ๒๕๖๐\\n(เมื่อพ้น ๖๐ วัน)', '๑ มกราคม พ.ศ. ๒๕๖๕' → date (ค.ศ.)"""
    if isinstance(s, date):
        return s
    t = str(s or "").translate(THAI_DIGITS).replace("พ.ศ.", " ")
    m = re.search(r"(\d{1,2})\s*([ก-๙.]+?)\s*-?\s*(\d{4})", t)
    if not m:
        return None
    month = MONTHS.get(m.group(2)) or MONTHS_SHORT.get(m.group(2))
    if not month:
        return None
    y = int(m.group(3))
    y = y - 543 if y > 2400 else y
    try:
        return date(y, month, int(m.group(1)))
    except ValueError:
        return None


def parse_sections(s) -> list[int]:
    """'11 (1) (4)' → [11]; '33, 50' → [33, 50]; '14 วรรคหก' → [14] (ตัดเลขในวงเล็บ/หลังคำว่าวรรค/อนุ)"""
    t = str(s or "").translate(THAI_DIGITS)
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"(วรรค|อนุ)\S*", " ", t)
    out: list[int] = []
    for n in re.findall(r"\d{1,3}", t):
        v = int(n)
        if 1 <= v <= 200 and v not in out:
            out.append(v)
    return out


def normalize_status(status, note) -> str:
    s = f"{status or ''} {note or ''}"
    if "ยกเลิก" in s:
        return "REPEALED"
    if "ใช้บังคับ" in s:
        return "IN_FORCE"
    return "UNKNOWN"


@dataclass
class RegisterRow:
    sheet_row: int
    reg_no: str
    group_no: str
    doc_type: str | None
    title_th: str
    authority_text: str | None
    authority_secs: list[int]
    owner_unit: str | None
    effective_text: str | None
    effective_date: date | None
    review_text: str | None
    status: str
    status_text: str | None
    status_assumed: bool
    note: str | None
    hearing_text: str | None
    attachments: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def _find_header(ws, wanted: dict) -> tuple[int, dict[str, int]]:
    for r in range(1, 15):
        cols = {}
        for c in range(1, ws.max_column + 1):
            k = _key(ws.cell(r, c).value)
            if k in wanted:
                cols[wanted[k]] = c
        if "reg_no" in cols or "pdf" in cols:
            return r, cols
    raise ValueError(f"ไม่พบแถวหัวตารางในแผ่น {ws.title}")


def read_workbook(path: str | Path) -> tuple[list[RegisterRow], str]:
    import openpyxl  # import ในฟังก์ชัน: API ไม่ต้องโหลดถ้าไม่ได้นำเข้า

    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
    ws = wb.worksheets[0]
    sheet_title = clean(next((ws.cell(r, 1).value for r in range(1, 5) if ws.cell(r, 1).value), "")) or ""
    hdr, cols = _find_header(ws, HEADERS)
    missing = {"reg_no", "title"} - cols.keys()
    if missing:
        raise ValueError(f"ไม่พบคอลัมน์ที่จำเป็น: {missing}")

    stats: dict[tuple[str, str], dict] = {}
    if "สถิติ" in wb.sheetnames:
        ss = wb["สถิติ"]
        shdr, scols = _find_header(ss, {**STAT_HEADERS, "ลำดับ": "reg_no", "รายชื่อกฎหมาย": "title"})
        for r in range(shdr + 1, ss.max_row + 1):
            no, t = clean(ss.cell(r, scols.get("reg_no", 1)).value), clean(ss.cell(r, scols.get("title", 3)).value)
            if not no or not t:
                continue
            counts = {}
            for k in STAT_HEADERS.values():
                v = ss.cell(r, scols[k]).value if k in scols else None
                if isinstance(v, (int, float)) and v:
                    counts[k] = int(v)
            stats[(no, t)] = counts

    get = lambda r, k: ws.cell(r, cols[k]).value if k in cols else None  # noqa: E731
    out: list[RegisterRow] = []
    seen: dict[tuple[str, str], int] = {}
    for r in range(hdr + 1, ws.max_row + 1):
        raw_no, raw_title = get(r, "reg_no"), get(r, "title")
        if raw_no is None and raw_title is None:
            continue
        reg_no = clean(raw_no) or ""
        issues: list[str] = []
        if isinstance(raw_no, float):
            # Excel เก็บ "163.10" เป็นตัวเลข 163.1 → เดาจากลำดับก่อนหน้า และแจ้งให้จัดรูปแบบเซลล์เป็นข้อความ
            reg_no = f"{raw_no:g}"
            prev = out[-1].reg_no if out else ""
            g, _, sub = reg_no.partition(".")
            pg, _, psub = prev.partition(".")
            if len(sub) == 1 and g == pg and psub.isdigit() and int(psub) >= 9 and int(sub + "0") >= int(psub) - 1:
                cand = sub + "0"
                if cand != sub:
                    reg_no = f"{g}.{cand}"
                    issues.append(f"เลขลำดับถูก Excel แปลงเป็นตัวเลข ({raw_no:g}) — ตีความเป็น {reg_no}")
        title = clean(raw_title) or ""
        if not reg_no:
            issues.append("ไม่มีเลขลำดับ")
        if not title:
            issues.append("ไม่มีชื่อกฎหมาย")
            title = "(ไม่มีชื่อ)"
        doc_type = clean(get(r, "doc_type"))
        eff_raw = get(r, "effective")
        eff_text = clean(eff_raw)
        eff = parse_thai_date(eff_raw)
        if eff_text and not eff:
            issues.append("วันใช้บังคับไม่อยู่ในรูปแบบวันที่")
        elif not eff_text and doc_type and not (doc_type and "/" in doc_type):
            issues.append("ไม่ระบุวันใช้บังคับ")
        auth = clean(get(r, "authority"))
        # แถวหัวกลุ่ม เช่น "ระเบียบ/ประกาศ ... เกี่ยวกับการจัดหาไฟฟ้า" ไม่ใช่กฎหมายรายฉบับ
        heading = bool(doc_type and "/" in doc_type and not auth and not eff_text)
        secs = parse_sections(auth)
        if doc_type and not auth and not heading:
            issues.append("ไม่ระบุบทอาศัยอำนาจ")
        status_text, note = clean(get(r, "status")), clean(get(r, "note"))
        status = normalize_status(status_text, note)
        if heading:
            status = "GROUP"
        assumed = False
        if status == "UNKNOWN" and doc_type:
            if ASSUME_IN_FORCE:
                status, assumed = "IN_FORCE", True
                issues.append("ไม่ระบุสถานะปัจจุบัน (ระบบถือว่าใช้บังคับไว้ก่อน)")
            else:
                issues.append("ไม่ระบุสถานะปัจจุบัน")
        if GARBLED.search(title):
            issues.append("ชื่อกฎหมายอาจคัดลอกจาก PDF ไม่สมบูรณ์ (สระ/วรรณยุกต์/เลขปีหาย)")
        if "\n" in str(raw_title or ""):
            issues.append("ชื่อกฎหมายมีการขึ้นบรรทัด")
        if doc_type in ("ระเบียบ", "ประกาศ") and not title.startswith(doc_type):
            issues.append(f"ประเภท '{doc_type}' ไม่ตรงกับชื่อเรื่อง")
        dup = seen.get((reg_no, title))
        if dup:
            issues.append(f"ซ้ำกับแถว {dup} ในทะเบียน")
        seen.setdefault((reg_no, title), r)
        hear = " · ".join(x for x in [
            f"รับฟังหลักการ {clean(get(r, 'hear_principle'))}" if clean(get(r, "hear_principle")) else None,
            f"รับฟังร่าง {clean(get(r, 'hear_draft'))}" if clean(get(r, "hear_draft")) else None,
        ] if x) or None
        out.append(RegisterRow(
            sheet_row=r, reg_no=reg_no, group_no=reg_no.split(".")[0] if reg_no else "-",
            doc_type=doc_type, title_th=title, authority_text=auth, authority_secs=secs,
            owner_unit=clean(get(r, "unit")), effective_text=eff_text, effective_date=eff,
            review_text=clean(get(r, "review")), status=status, status_text=status_text, status_assumed=assumed, note=note,
            hearing_text=hear, attachments=stats.get((reg_no, title), {}), issues=issues,
        ))
    return out, sheet_title


def chunk_text(r: dict) -> tuple[str, str]:
    """ข้อความที่ใช้ index สำหรับค้นหา/ผู้ช่วย AI (เฉพาะข้อมูลที่เปิดเผยได้)"""
    status = {"IN_FORCE": "ใช้บังคับ", "REPEALED": "ยกเลิกแล้ว"}.get(r["status"], "ไม่ระบุสถานะในทะเบียน")
    if r.get("status_assumed"):
        status = "ใช้บังคับ (ทะเบียนไม่ระบุสถานะ ระบบสันนิษฐานว่ายังใช้บังคับ — ควรตรวจสอบ)"
    secs = ", ".join(f"มาตรา {s}" for s in r["authority_secs"]) or "-"
    body = (f"{r['title_th']}\nประเภท: {r['doc_type'] or '-'}\n"
            f"ออกโดยอาศัยอำนาจตาม พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550 {secs}"
            f" (บทอาศัยอำนาจตามทะเบียน: {r['authority_text'] or '-'})\n"
            f"วันใช้บังคับ: {r['effective_text'] or '-'}\nสถานะ: {status}\n"
            + ("หมายเหตุ: ระเบียนจากทะเบียนคุมกฎหมายลำดับรอง มีตัวบทเต็มในระบบ" if r.get("instrument_eli") else
               "หมายเหตุ: ระเบียนจากทะเบียนคุมกฎหมายลำดับรอง ยังไม่มีตัวบทเต็มในระบบ"))
    return f"ทะเบียนกฎหมาย {r['reg_no']} {r['title_th']}", body
