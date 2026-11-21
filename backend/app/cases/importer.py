"""นำเข้าทะเบียนคดีจาก Excel: ดาวน์โหลดแม่แบบ → อัปโหลด → ระบบตรวจทีละแถว → ส่งตรวจ → ผู้อื่นอนุมัติ → บันทึก

- จับคู่คดีด้วย (ศาล, เลขคดีดำ): มีอยู่แล้ว = ปรับปรุง, ยังไม่มี = เพิ่มใหม่ — นำเข้าไฟล์เดิมซ้ำไม่สร้างคดีซ้ำ
- ชีต "กระบวนพิจารณา" (ไม่บังคับ): เพิ่มรายการที่ยังไม่มี (วันที่ + รายการเดียวกันถือว่ามีแล้ว)
- หัวคอลัมน์ยังเป็นรุ่นตั้งต้นตามโครงสร้างตาราง court_case — ปรับให้ตรงทะเบียนจริงเมื่อฝ่ายกฎหมายส่งหัวคอลัมน์มา
- ไม่รับบนเครื่อง PUBLIC_DATA_ONLY (ทะเบียนคดีเป็นข้อมูลภายใน)
"""
import io
import json
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..config import public_data_only
from ..db import get_conn, one, rows
from ..search.indexer import reindex_cases
from . import manage

router = APIRouter(prefix="/api/v1/court-cases/imports", tags=["court-case-imports"])

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 2000
CASE_SHEET, PROC_SHEET, HELP_SHEET = "คดี", "กระบวนพิจารณา", "คำอธิบาย"
# (field, หัวคอลัมน์, บังคับ, คำอธิบาย)
CASE_COLS = [
    ("black_no", "เลขคดีดำ", True, "เช่น คบ. 425/2568 — ใช้คู่กับศาลเป็นตัวจับคู่คดี"),
    ("red_no", "เลขคดีแดง", False, "เมื่อศาลมีคำพิพากษาแล้ว"),
    ("court_name", "ศาล", True, "เช่น ศาลปกครองกลาง"),
    ("court_level", "ชั้นศาล", True, " / ".join(manage.COURT_LEVELS)),
    ("case_category", "ประเภทคดี", False, "ค่าเริ่มต้น: คดีปกครอง"),
    ("subject", "เรื่อง", True, "ประเด็นพิพาทโดยย่อ"),
    ("summary", "สรุปข้อเท็จจริง", False, "ไม่ใส่ = ใช้ข้อความในคอลัมน์เรื่อง"),
    ("party_code", "รหัสคู่กรณี", True, "รหัสนามแฝง ห้ามใส่ชื่อจริง เช่น EL-VSPP-218"),
    ("party_label", "คำเรียกคู่กรณี", False, "เช่น ผู้ฟ้องคดี (ผู้ผลิตไฟฟ้า รหัส [EL-VSPP-218]) — ห้ามใส่ชื่อจริง"),
    ("defendant", "ผู้ถูกฟ้องคดี", False, "เช่น คณะกรรมการกำกับกิจการพลังงาน (ผู้ถูกฟ้องคดี)"),
    ("energy_sector", "ประเภทกิจการพลังงาน", True, "เช่น ไฟฟ้า / ก๊าซธรรมชาติ"),
    ("incident_date", "วันเกิดเหตุ", False, "วันที่ใน Excel หรือ 30/01/2568 หรือ 30 ม.ค. 2568"),
    ("filed_date", "วันยื่นฟ้อง", False, ""),
    ("judgment_date", "วันพิพากษา", False, ""),
    ("status", "สถานะ", False, "อยู่ระหว่างพิจารณา (ค่าเริ่มต้น) / ถึงที่สุด"),
    ("stage_text", "ขั้นตอนคดี", False, "เช่น อยู่ระหว่างแสวงหาข้อเท็จจริง"),
    ("result", "ผลคดี", False, ""),
    ("classification", "ชั้นความลับ", False, "ใช้ภายใน (ค่าเริ่มต้น) / จำกัดเฉพาะผู้รับผิดชอบ"),
    ("origin_appeal_no", "เลขเรื่องอุทธรณ์ต้นเรื่อง", False, "เช่น อธ. 1/2568 — ต้องมีในระบบแล้ว"),
]
PROC_COLS = [
    ("black_no", "เลขคดีดำ", True, ""),
    ("court_name", "ศาล", True, ""),
    ("proceeding_date", "วันที่", True, ""),
    ("title", "รายการ", True, "เช่น ยื่นคำให้การ / นั่งพิจารณาคดีครั้งแรก"),
    ("detail", "รายละเอียด", False, ""),
]


def _key(h) -> str:
    return re.sub(r"[\s*]", "", str(h or ""))


# ---------------------------------------------------------------- template
@router.get("/template")
def template(user: User = Depends(current_user)):
    authz.require_internal(user)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    req_fill, opt_fill = PatternFill("solid", fgColor="FDE68A"), PatternFill("solid", fgColor="E2E8F0")

    def sheet(ws, cols):
        for i, (_, h, req, _) in enumerate(cols, 1):
            c = ws.cell(1, i, f"{h}*" if req else h)
            c.font, c.fill = Font(bold=True), req_fill if req else opt_fill
            ws.column_dimensions[c.column_letter].width = max(14, len(h) + 6)
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = CASE_SHEET
    sheet(ws, CASE_COLS)
    col = {f: i for i, (f, *_rest) in enumerate(CASE_COLS, 1)}
    for f, values in (("court_level", manage.COURT_LEVELS), ("status", manage.STATUS.values()),
                      ("classification", manage.CLASSES.values())):
        letter = ws.cell(1, col[f]).column_letter
        dv = DataValidation(type="list", formula1='"' + ",".join(values) + '"', allow_blank=True)
        dv.add(f"{letter}2:{letter}{MAX_ROWS + 1}")
        ws.add_data_validation(dv)
    sheet(wb.create_sheet(PROC_SHEET), PROC_COLS)
    hs = wb.create_sheet(HELP_SHEET)
    hs.append(["ชีต", "คอลัมน์", "บังคับ", "คำอธิบาย"])
    for name, cols in ((CASE_SHEET, CASE_COLS), (PROC_SHEET, PROC_COLS)):
        for _, h, req, note in cols:
            hs.append([name, h, "ใช่" if req else "", note])
    hs.append([])
    hs.append(["", "ข้อควรระวัง", "", "ห้ามใส่ชื่อจริง เลขประจำตัวประชาชน หรือที่อยู่ของคู่กรณี — ระบบเก็บเฉพาะรหัสนามแฝง"])
    hs.append(["", "การจับคู่", "", "ศาล + เลขคดีดำ ตรงกับคดีในระบบ = ปรับปรุงคดีนั้น; ไม่ตรง = เพิ่มคดีใหม่"])
    for c in hs[1]:
        c.font = Font(bold=True)
    for w, letter in ((14, "A"), (26, "B"), (8, "C"), (90, "D")):
        hs.column_dimensions[letter].width = w
    for r in hs.iter_rows(min_row=2):
        r[3].alignment = Alignment(wrap_text=True)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename*=UTF-8''case-import-template.xlsx"})


# ---------------------------------------------------------------- parse + validate
def _read_sheet(ws, cols, required_sheet: bool) -> tuple[list[dict], list[str]]:
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if header is None:
        return [], ([f"ชีต {ws.title} ว่าง"] if required_sheet else [])
    by_head = {_key(h): f for f, h, *_ in cols}
    idx = {i: by_head[_key(h)] for i, h in enumerate(header) if _key(h) in by_head}
    missing = [h for f, h, req, _ in cols if req and f not in idx.values()]
    warnings = [f"ชีต {ws.title}: ไม่รู้จักคอลัมน์ {h}" for i, h in enumerate(header) if h and i not in idx]
    if missing:
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED",
                                         "detail": f"ชีต {ws.title} ไม่มีคอลัมน์ที่บังคับ: {', '.join(missing)} — ใช้แม่แบบของระบบ"})
    out = []
    for n, r in enumerate(it, start=2):
        if r is None or all(v is None or str(v).strip() == "" for v in r):
            continue
        out.append({"row": n, **{f: r[i] for i, f in idx.items() if i < len(r)}})
        if len(out) > MAX_ROWS:
            raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": f"เกิน {MAX_ROWS} แถวต่อไฟล์ — แบ่งไฟล์"})
    return out, warnings


def _jsonable(v: dict) -> dict:
    return {k: (x.isoformat() if hasattr(x, "isoformat") else x) for k, x in v.items()}


def validate(conn: Connection, user: User, case_rows: list[dict], proc_rows: list[dict]) -> tuple[list[dict], list[dict], int]:
    on = manage.today()
    seen: dict[tuple[str, str], int] = {}
    cases = []
    for r in case_rows:
        v, errs = manage.normalize(conn, r, on)
        warns = []
        key = (v["court_name"] or "", v["black_no"] or "")
        if key in seen:
            errs.append(f"ซ้ำกับแถวที่ {seen[key]}")
        seen.setdefault(key, r["row"])
        action, cid = "create", None
        ex = manage.find_case(conn, *key) if all(key) else None
        if ex:
            action, cid = "update", ex["case_id"]
            owners = manage.owners_of(conn, cid)
            if not authz.can_edit_workflow(user, owners):
                errs.append("คดีนี้มีในระบบแล้ว แต่คุณไม่ใช่ผู้รับผิดชอบ — ให้ผู้รับผิดชอบหรือ ผอ.คบ. นำเข้า")
            elif all(v[f] == ex[f] for f in manage.FIELDS):
                action = "unchanged"
        if v["filed_date"] and v["incident_date"] and v["filed_date"] < v["incident_date"]:
            warns.append("วันยื่นฟ้องก่อนวันเกิดเหตุ")
        if v["status"] == "FINAL" and not v["red_no"]:
            warns.append("ถึงที่สุดแต่ไม่มีเลขคดีแดง")
        if v["classification"] == "restricted" and user.role != "LGL" and action == "create":
            errs.append("คดีชั้นจำกัดเฉพาะผู้รับผิดชอบที่เพิ่มใหม่ต้องมีผู้รับผิดชอบ — ให้นิติกรผู้รับผิดชอบนำเข้า")
        cases.append({"row": r["row"], "action": action, "case_id": cid, "values": _jsonable(v), "errors": errs,
                      "warnings": warns, "origin_appeal_no": manage._s(r.get("origin_appeal_no"))})
    procs = []
    for r in proc_rows:
        errs = []
        court, black = manage._s(r.get("court_name")), manage._s(r.get("black_no"))
        try:
            d = manage.to_date(r.get("proceeding_date"))
        except ValueError:
            d = None
        if not d:
            errs.append("ต้องระบุวันที่ที่อ่านได้")
        title = manage._s(r.get("title"))
        if not title:
            errs.append("ต้องระบุรายการ")
        elif len(title) > 300:
            errs.append("รายการยาวเกิน 300 อักษร")
        in_file = (court or "", black or "") in seen
        if not in_file and not (court and black and manage.find_case(conn, court, black)):
            errs.append("ไม่พบคดีนี้ทั้งในชีตคดีและในระบบ")
        procs.append({"row": r["row"], "court_name": court, "black_no": black, "proceeding_date": d.isoformat() if d else None,
                      "title": title, "detail": manage._s(r.get("detail")), "errors": errs})
    n_err = sum(len(c["errors"]) > 0 for c in cases) + sum(len(p["errors"]) > 0 for p in procs)
    return cases, procs, n_err


@router.post("", status_code=201)
async def upload(file: UploadFile = File(...), user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    if not manage.can_create(user):
        raise authz.forbidden("นำเข้าได้เฉพาะนิติกรฝ่ายคดีหรือ ผอ.คบ.")
    if public_data_only():
        raise HTTPException(400, detail={"code": "PUBLIC_DATA_ONLY",
                                         "detail": "เครื่องนี้เก็บเฉพาะข้อมูลสาธารณะ — นำเข้าทะเบียนคดีบนเครื่องของสำนักงาน"})
    name = Path(file.filename or "file").name
    if Path(name).suffix.lower() != ".xlsx":
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "รองรับเฉพาะไฟล์ .xlsx (ใช้แม่แบบของระบบ)"})
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, detail={"code": "TOO_LARGE", "detail": "ไฟล์ใหญ่เกิน 10 MB"})
    if not data.startswith(b"PK"):
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "ไฟล์ไม่ใช่ Excel (.xlsx)"})
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "เปิดไฟล์ Excel ไม่ได้"}) from e
    if CASE_SHEET not in wb.sheetnames:
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": f"ไม่พบชีต '{CASE_SHEET}' — ใช้แม่แบบของระบบ"})
    case_rows, warns = _read_sheet(wb[CASE_SHEET], CASE_COLS, True)
    proc_rows, w2 = _read_sheet(wb[PROC_SHEET], PROC_COLS, False) if PROC_SHEET in wb.sheetnames else ([], [])
    if not case_rows and not proc_rows:
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "ไม่มีข้อมูลในไฟล์"})
    cases, procs, n_err = validate(conn, user, case_rows, proc_rows)
    from ..seed.raw_files import add_file
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "import.xlsx"
        tmp.write_bytes(data)
        st = add_file(conn, tmp, "internal", source="นำเข้าทะเบียนคดี", received_from=user.user_id,
                      original_name=name, added_by=user.user_id)
    iid = conn.execute(text("""INSERT INTO case_import (original_name, sha256, cases, proceedings, error_count, created_by, result)
                               VALUES (:n, :s, CAST(:c AS jsonb), CAST(:p AS jsonb), :e, :u, CAST(:r AS jsonb))
                               RETURNING import_id"""),
                       {"n": name, "s": st["sha256"], "c": json.dumps(cases, ensure_ascii=False),
                        "p": json.dumps(procs, ensure_ascii=False), "e": n_err, "u": user.user_id,
                        "r": json.dumps({"warnings": warns + w2}, ensure_ascii=False)}).scalar()
    audit.record(conn, user, "court_case.import.create", f"case_import/{iid}",
                 detail={"name": name, "sha256": st["sha256"], "cases": len(cases), "proceedings": len(procs), "errors": n_err})
    return _view(_get(conn, iid), user)


# ---------------------------------------------------------------- review + apply
def _get(conn: Connection, iid: int) -> dict:
    r = one(conn, "SELECT * FROM case_import WHERE import_id = :i", i=iid)
    if not r:
        raise authz.not_found()
    return r


def _view(r: dict, user: User, full: bool = True) -> dict:
    d = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()
         if full or k not in ("cases", "proceedings")}
    cs = r["cases"]
    d["counts"] = {a: sum(c["action"] == a for c in cs) for a in ("create", "update", "unchanged")}
    d["counts"]["proceedings"] = len(r["proceedings"])
    d["can_approve"] = (r["status"] == "submitted" and user.user_id not in (r["created_by"], r["submitted_by"])
                        and manage.can_create(user))
    return d


def _require(user: User) -> None:
    authz.require_internal(user)
    if not manage.can_create(user):
        raise authz.forbidden("เฉพาะนิติกรฝ่ายคดีหรือ ผอ.คบ.")


@router.get("")
def list_imports(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _require(user)
    return {"items": [_view(r, user, full=False)
                      for r in rows(conn, "SELECT * FROM case_import ORDER BY import_id DESC LIMIT 100")]}


@router.get("/{iid}")
def get_import(iid: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _require(user)
    return _view(_get(conn, iid), user)


@router.post("/{iid}/submit")
def submit(iid: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _require(user)
    r = _get(conn, iid)
    if r["status"] != "draft":
        raise HTTPException(409, detail={"code": "CONFLICT", "detail": "ส่งตรวจได้เฉพาะรายการที่ยังไม่ส่ง"})
    if r["error_count"]:
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "ยังมีแถวที่ผิด — แก้ในไฟล์แล้วนำเข้าใหม่"})
    conn.execute(text("UPDATE case_import SET status = 'submitted', submitted_by = :u, submitted_at = now() WHERE import_id = :i"),
                 {"u": user.user_id, "i": iid})
    audit.record(conn, user, "court_case.import.submit", f"case_import/{iid}")
    return _view(_get(conn, iid), user)


class Review(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/{iid}/reject")
def reject(iid: int, body: Review, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ไม่รับ / ยกเลิก (ผู้นำเข้ายกเลิกเองได้)"""
    _require(user)
    r = _get(conn, iid)
    if r["status"] not in ("draft", "submitted"):
        raise HTTPException(409, detail={"code": "CONFLICT", "detail": "รายการนี้ปิดแล้ว"})
    conn.execute(text("""UPDATE case_import SET status = 'rejected', reviewed_by = :u, reviewed_at = now(), review_note = :n
                         WHERE import_id = :i"""), {"u": user.user_id, "n": body.note, "i": iid})
    audit.record(conn, user, "court_case.import.reject", f"case_import/{iid}", detail={"note": body.note})
    return _view(_get(conn, iid), user)


@router.post("/{iid}/approve")
def approve(iid: int, body: Review, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _require(user)
    r = _get(conn, iid)
    if r["status"] != "submitted":
        raise HTTPException(409, detail={"code": "CONFLICT", "detail": "อนุมัติได้เฉพาะรายการที่ส่งตรวจแล้ว"})
    if user.user_id in (r["created_by"], r["submitted_by"]):
        raise authz.forbidden("ผู้อนุมัติต้องไม่ใช่ผู้นำเข้าหรือผู้ส่งตรวจ (หลักสี่ตา)")
    # ตรวจซ้ำกับข้อมูลปัจจุบัน (อาจมีคนแก้คดีระหว่างรออนุมัติ) — สิทธิ์แก้คดีเดิมตัดสินตามผู้นำเข้า
    from ..auth import DEMO_USERS
    importer = DEMO_USERS.get(r["created_by"]) or user
    raw_cases = [{"row": c["row"], **c["values"], "origin_appeal_no": c.get("origin_appeal_no")} for c in r["cases"]]
    raw_procs = [{"row": p["row"], **{k: p[k] for k in ("black_no", "court_name", "proceeding_date", "title", "detail")}}
                 for p in r["proceedings"]]
    cases, procs, n_err = validate(conn, importer, raw_cases, raw_procs)
    if n_err:
        raise HTTPException(409, detail={"code": "STALE", "detail": "ข้อมูลในระบบเปลี่ยนไปหลังตรวจ — ให้ผู้นำเข้านำเข้าไฟล์ใหม่"})
    actor = user.user_id
    created, updated, before = [], [], {}   # before[case_id] = ภาพรวมก่อนนำเข้า (None = คดีใหม่)
    for c in cases:
        if c["action"] == "unchanged":
            continue
        v, _ = manage.normalize(conn, {**c["values"], "origin_appeal_no": c.get("origin_appeal_no")}, manage.today())
        if c["action"] == "create":
            cid = manage.insert_case(conn, v, r["created_by"])
            if importer.role == "LGL":
                manage.set_owners(conn, cid, [importer.user_id])
            before[cid] = None
            created.append(cid)
        else:
            cid = c["case_id"]
            before[cid] = manage.snapshot(conn, cid)
            manage.update_case(conn, cid, v, r["created_by"])
            updated.append(cid)
    n_proc = 0
    for p in procs:
        k = manage.find_case(conn, p["court_name"], p["black_no"])
        if conn.execute(text("""SELECT 1 FROM court_proceeding WHERE case_id = :c AND proceeding_date = :d AND title = :t"""),
                        {"c": k["case_id"], "d": p["proceeding_date"], "t": p["title"]}).first():
            continue
        if k["case_id"] not in before:
            before[k["case_id"]] = manage.snapshot(conn, k["case_id"])
        conn.execute(text("""INSERT INTO court_proceeding (case_id, proceeding_date, title, detail) VALUES (:c, :d, :t, :x)"""),
                     {"c": k["case_id"], "d": p["proceeding_date"], "t": p["title"], "x": p["detail"]})
        n_proc += 1
    touched = set(before)
    for cid, snap in before.items():
        manage.add_revision(conn, cid, "import", actor, snap)
    for cid in touched:
        reindex_cases(conn, only=("court_case", cid))
    result = {**(r["result"] or {}), "created": created, "updated": updated, "proceedings_added": n_proc}
    conn.execute(text("""UPDATE case_import SET status = 'applied', reviewed_by = :u, reviewed_at = now(), review_note = :n,
                         result = CAST(:r AS jsonb) WHERE import_id = :i"""),
                 {"u": user.user_id, "n": body.note, "r": json.dumps(result, ensure_ascii=False), "i": iid})
    audit.record(conn, user, "court_case.import.approve", f"case_import/{iid}",
                 detail={"created": len(created), "updated": len(updated), "proceedings": n_proc,
                         "imported_by": r["created_by"]})
    return _view(_get(conn, iid), user)
