"""เพิ่ม/แก้ไขคดีจากหน้าเว็บ ลำดับกระบวนพิจารณา เอกสารในสำนวน และประวัติการแก้ไข

สิทธิ์: เพิ่มคดี = นิติกรฝ่ายคดี (LGL) หรือ ผอ.คบ. (DIR); แก้ไข = เจ้าของเรื่อง (LGL) หรือ ผอ.คบ. (R4 เดียวกับงานอุทธรณ์)
ชื่อจริงของคู่กรณีไม่เก็บในระบบ — ใช้รหัสนามแฝง (party_code) เหมือนงานอุทธรณ์
ทุกการแก้ไขบันทึก audit และเก็บภาพรวมของคดีเป็นรุ่นใน court_case_revision (แก้/ลบไม่ได้)
เครื่อง PUBLIC_DATA_ONLY: คดีที่เพิ่มถูกติดป้ายข้อมูลสาธิต และไม่รับไฟล์เอกสารหรือการนำเข้าจาก Excel
"""
import json
import logging
import re
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import audit, authz, clock
from ..auth import DEMO_USERS, User, current_user
from ..config import public_data_only
from ..db import get_conn, get_engine, one, rows
from ..law.register import parse_thai_date
from ..search.indexer import reindex_cases
from ..storage import RawStore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/court-cases", tags=["court-cases"])

COURT_LEVELS = ("ศาลปกครองชั้นต้น", "ศาลปกครองสูงสุด", "ศาลชั้นต้น (ศาลยุติธรรม)", "ศาลอุทธรณ์", "ศาลฎีกา")
STATUS = {"PENDING": "อยู่ระหว่างพิจารณา", "FINAL": "ถึงที่สุด"}
CLASSES = {"internal": "ใช้ภายใน", "restricted": "จำกัดเฉพาะผู้รับผิดชอบ"}
DOC_TYPES = ("คำฟ้อง", "คำให้การ", "คำคัดค้านคำให้การ", "คำแถลง", "คำสั่งศาล", "คำพิพากษา", "อื่น ๆ")
DOC_EXTS = (".pdf", ".docx", ".doc")
MAX_DOC_BYTES = 50 * 1024 * 1024
FIELDS = ("black_no", "red_no", "court_level", "court_name", "case_category", "subject", "summary", "party_code",
          "party_label", "defendant", "energy_sector", "incident_date", "filed_date", "judgment_date", "status",
          "stage_text", "result", "classification", "appeal_id")
DATES = ("incident_date", "filed_date", "judgment_date")
LABEL = {"incident_date": "วันเกิดเหตุ", "filed_date": "วันยื่นฟ้อง", "judgment_date": "วันพิพากษา", "black_no": "เลขคดีดำ",
         "red_no": "เลขคดีแดง", "court_name": "ศาล", "case_category": "ประเภทคดี", "party_code": "รหัสคู่กรณี",
         "energy_sector": "ประเภทกิจการพลังงาน"}


def today() -> date:
    return clock.today()


def can_create(user: User) -> bool:
    return user.role in ("LGL", "DIR")


# ---------------------------------------------------------------- normalize (ใช้ร่วมกับการนำเข้า Excel)
def to_date(v) -> date | None:
    """date / datetime จาก Excel (ปี พ.ศ. ก็ได้) / '2026-01-30' / '30/01/2569' / '30 ม.ค. 2569'"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.replace(year=v.year - 543) if v.year > 2400 else v
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s)
        if not m:
            r = parse_thai_date(s)
            if r is None:
                raise ValueError(s)
            return r
        d, mo, y = map(int, m.groups())
    y = y - 543 if y > 2400 else y
    return date(y, mo, d)


def _s(v) -> str | None:
    if v is None:
        return None
    t = re.sub(r"\s+", " ", str(v)).strip()
    return t or None


def norm_no(no: str) -> str:
    return re.sub(r"[\s.]", "", no)


def normalize(conn: Connection, raw: dict, on: date) -> tuple[dict, list[str]]:
    """ตรวจและจัดรูปข้อมูลคดี 1 เรื่อง → (ค่าที่จะบันทึก, ข้อผิดพลาด)"""
    errs: list[str] = []
    v = {k: _s(raw.get(k)) for k in FIELDS if k not in DATES and k != "appeal_id"}
    for k in DATES:
        try:
            v[k] = to_date(raw.get(k))
        except ValueError:
            errs.append(f"{LABEL[k]}: อ่านวันที่ไม่ได้ ({raw.get(k)})")
            v[k] = None
        if v[k] and v[k] > on:
            errs.append(f"{LABEL[k]}: เป็นวันในอนาคต")
    for k, label in (("black_no", "เลขคดีดำ"), ("court_name", "ศาล"), ("subject", "เรื่อง"),
                     ("party_code", "รหัสคู่กรณี"), ("energy_sector", "ประเภทกิจการพลังงาน")):
        if not v[k]:
            errs.append(f"ต้องระบุ{label}")
    if v["court_level"] not in COURT_LEVELS:
        errs.append(f"ชั้นศาลต้องเป็น {' / '.join(COURT_LEVELS)}")
    st = {**{k: k for k in STATUS}, **{t: k for k, t in STATUS.items()}}.get(v["status"] or "PENDING")
    if not st:
        errs.append("สถานะต้องเป็น อยู่ระหว่างพิจารณา หรือ ถึงที่สุด")
    v["status"] = st or "PENDING"
    cls = {**{k: k for k in CLASSES}, **{t: k for k, t in CLASSES.items()}}.get(v["classification"] or "internal")
    if not cls:
        errs.append("ชั้นความลับต้องเป็น ใช้ภายใน หรือ จำกัดเฉพาะผู้รับผิดชอบ")
    v["classification"] = cls or "internal"
    for k, n in (("black_no", 50), ("red_no", 50), ("court_name", 100), ("case_category", 30), ("party_code", 20),
                 ("energy_sector", 40)):
        if v[k] and len(v[k]) > n:
            errs.append(f"{LABEL[k]}: ยาวเกิน {n} อักษร")
    if v["party_code"] and not re.fullmatch(r"[A-Za-z0-9ก-๙._/-]+", v["party_code"]):
        errs.append("รหัสคู่กรณีต้องเป็นรหัส ไม่ใช่ชื่อ (ตัวอักษร ตัวเลข - _ / . ไม่มีเว้นวรรค)")
    v["case_category"] = v["case_category"] or "คดีปกครอง"
    v["summary"] = v["summary"] or v["subject"]
    v["party_label"] = v["party_label"] or (f"ผู้ฟ้องคดี รหัส [{v['party_code']}]" if v["party_code"] else None)
    v["appeal_id"] = None
    origin = _s(raw.get("origin_appeal_no"))
    if origin:
        v["appeal_id"] = conn.execute(text("SELECT appeal_id FROM appeal WHERE appeal_no_norm = :n"),
                                      {"n": norm_no(origin)}).scalar()
        if v["appeal_id"] is None:
            errs.append(f"ไม่พบเรื่องอุทธรณ์ต้นเรื่อง {origin}")
    return v, errs


def lgl_owners(owners: list[str] | None) -> list[str]:
    out = []
    for u in owners or []:
        if u not in DEMO_USERS or DEMO_USERS[u].role != "LGL":
            raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": f"ผู้รับผิดชอบต้องเป็นนิติกรฝ่ายคดี ({u})"})
        if u not in out:
            out.append(u)
    return out


# ---------------------------------------------------------------- write helpers (ใช้ร่วมกับการนำเข้า)
def find_case(conn: Connection, court_name: str, black_no: str) -> dict | None:
    return one(conn, "SELECT * FROM court_case WHERE court_name = :c AND black_no = :b", c=court_name, b=black_no)


def owners_of(conn: Connection, case_id: int) -> list[str]:
    return [r["user_id"] for r in rows(conn, "SELECT user_id FROM court_case_owner WHERE case_id = :i ORDER BY user_id",
                                       i=case_id)]


def set_owners(conn: Connection, case_id: int, owners: list[str]) -> None:
    conn.execute(text("DELETE FROM court_case_owner WHERE case_id = :i"), {"i": case_id})
    for u in owners:
        conn.execute(text("INSERT INTO court_case_owner VALUES (:i, :u)"), {"i": case_id, "u": u})


def insert_case(conn: Connection, v: dict, actor: str) -> int:
    cols = ", ".join(FIELDS)
    vals = ", ".join(f":{k}" for k in FIELDS)
    return conn.execute(text(f"""INSERT INTO court_case ({cols}, is_demo, created_by)
                                 VALUES ({vals}, :demo, :actor) RETURNING case_id"""),
                        {**{k: v[k] for k in FIELDS}, "demo": public_data_only(), "actor": actor}).scalar()


def update_case(conn: Connection, case_id: int, v: dict, actor: str) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in FIELDS)
    conn.execute(text(f"""UPDATE court_case SET {sets}, row_version = row_version + 1, updated_by = :actor,
                          updated_at = now() WHERE case_id = :id"""),
                 {**{k: v[k] for k in FIELDS}, "actor": actor, "id": case_id})


def snapshot(conn: Connection, case_id: int) -> dict:
    k = one(conn, f"SELECT {', '.join(FIELDS)} FROM court_case WHERE case_id = :i", i=case_id)
    snap = {f: (x.isoformat() if hasattr(x, "isoformat") else x) for f, x in k.items()}
    snap["owners"] = owners_of(conn, case_id)
    snap["proceedings"] = [{"date": p["proceeding_date"].isoformat(), "title": p["title"], "detail": p["detail"]}
                           for p in rows(conn, """SELECT proceeding_date, title, detail FROM court_proceeding
                                                  WHERE case_id = :i ORDER BY proceeding_date, proceeding_id""", i=case_id)]
    return snap


def changes(old: dict | None, new: dict) -> dict:
    if old is None:
        return {}
    out: dict = {"fields": [{"field": k, "old": old.get(k), "new": new.get(k)}
                            for k in (*FIELDS, "owners") if old.get(k) != new.get(k)]}
    po = {(p["date"], p["title"]) for p in old.get("proceedings", [])}
    pn = {(p["date"], p["title"]) for p in new.get("proceedings", [])}
    if po != pn:
        out["proceedings_added"] = sorted(f"{d} {t}" for d, t in pn - po)
        out["proceedings_removed"] = sorted(f"{d} {t}" for d, t in po - pn)
    return out


def add_revision(conn: Connection, case_id: int, kind: str, actor: str, before: dict | None) -> dict:
    after = snapshot(conn, case_id)
    summary = changes(before, after)
    n = conn.execute(text("SELECT coalesce(max(rev_no), 0) + 1 FROM court_case_revision WHERE case_id = :i"),
                     {"i": case_id}).scalar()
    conn.execute(text("""INSERT INTO court_case_revision (case_id, rev_no, kind, snapshot, actor, summary)
                         VALUES (:i, :n, :k, CAST(:s AS jsonb), :a, CAST(:m AS jsonb))"""),
                 {"i": case_id, "n": n, "k": kind, "s": json.dumps(after, ensure_ascii=False), "a": actor,
                  "m": json.dumps(summary, ensure_ascii=False)})
    return summary


def _load(conn: Connection, user: User, case_id: int, edit: bool = False) -> tuple[dict, list[str]]:
    authz.require_internal(user)
    k = one(conn, "SELECT * FROM court_case WHERE case_id = :i", i=case_id)
    if not k:
        raise authz.not_found()
    owners = owners_of(conn, case_id)
    if not authz.can_read_case(user, k["classification"], owners):
        raise authz.not_found()
    if edit and not authz.can_edit_workflow(user, owners):
        raise authz.forbidden("แก้ไขได้เฉพาะนิติกรผู้รับผิดชอบคดีหรือ ผอ.คบ.")
    return k, owners


def _bad(errs: list[str]) -> HTTPException:
    return HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "; ".join(errs)})


# ---------------------------------------------------------------- API: คดี
class CaseIn(BaseModel):
    black_no: str = Field(min_length=1, max_length=50)
    red_no: str | None = Field(default=None, max_length=50)
    court_level: str = Field(max_length=50)
    court_name: str = Field(min_length=2, max_length=100)
    case_category: str | None = Field(default=None, max_length=30)
    subject: str = Field(min_length=3, max_length=1000)
    summary: str | None = Field(default=None, max_length=20000)
    party_code: str = Field(min_length=2, max_length=20)
    party_label: str | None = Field(default=None, max_length=300)
    defendant: str | None = Field(default=None, max_length=300)
    energy_sector: str = Field(min_length=1, max_length=40)
    incident_date: date | None = None
    filed_date: date | None = None
    judgment_date: date | None = None
    status: str = Field(default="PENDING", pattern="^(PENDING|FINAL)$")
    stage_text: str | None = Field(default=None, max_length=500)
    result: str | None = Field(default=None, max_length=2000)
    classification: str = Field(default="internal", pattern="^(internal|restricted)$")
    origin_appeal_no: str | None = Field(default=None, max_length=50)
    owners: list[str] | None = Field(default=None, max_length=10)


class CaseUpdate(CaseIn):
    row_version: int


@router.get("/form-options")
def form_options(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    sectors = [r["s"] for r in rows(conn, """SELECT DISTINCT energy_sector AS s FROM court_case UNION
                                             SELECT DISTINCT energy_sector FROM appeal ORDER BY 1""")]
    courts = [r["court_name"] for r in rows(conn, "SELECT DISTINCT court_name FROM court_case ORDER BY 1")]
    return {"court_levels": list(COURT_LEVELS), "statuses": STATUS, "classes": CLASSES, "doc_types": list(DOC_TYPES),
            "sectors": sectors, "courts": courts, "can_create": can_create(user), "can_assign": authz.can_assign(user),
            "officers": [{"user_id": u.user_id, "name": u.name} for u in DEMO_USERS.values() if u.role == "LGL"],
            "public_data_only": public_data_only()}


@router.post("", status_code=201)
def create_case(body: CaseIn, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    if not can_create(user):
        raise authz.forbidden("เพิ่มคดีได้เฉพาะนิติกรฝ่ายคดีหรือ ผอ.คบ.")
    v, errs = normalize(conn, body.model_dump(), today())
    if errs:
        raise _bad(errs)
    if body.owners and not authz.can_assign(user):
        raise authz.forbidden("เฉพาะ ผอ.คบ. เท่านั้นที่กำหนดผู้รับผิดชอบ")
    owners = lgl_owners(body.owners) if authz.can_assign(user) else [user.user_id]
    if v["classification"] == "restricted" and not owners:
        raise _bad(["คดีชั้นจำกัดเฉพาะผู้รับผิดชอบต้องมีผู้รับผิดชอบอย่างน้อย 1 คน"])
    if find_case(conn, v["court_name"], v["black_no"]):
        raise HTTPException(409, detail={"code": "DUPLICATE", "detail": f"มีคดีดำที่ {v['black_no']} ของ{v['court_name']} แล้ว"})
    cid = insert_case(conn, v, user.user_id)
    set_owners(conn, cid, owners)
    add_revision(conn, cid, "create", user.user_id, None)
    audit.record(conn, user, "court_case.create", f"court_case/{cid}",
                 detail={"black_no": v["black_no"], "court": v["court_name"], "classification": v["classification"]})
    reindex_cases(conn, only=("court_case", cid))
    return {"case_id": cid}


@router.put("/{case_id}")
def edit_case(case_id: int, body: CaseUpdate, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    k, owners = _load(conn, user, case_id, edit=True)
    if body.row_version != k["row_version"]:
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT", "detail": "มีผู้อื่นแก้ไขคดีนี้ระหว่างนี้ — โหลดใหม่แล้วแก้อีกครั้ง"})
    v, errs = normalize(conn, body.model_dump(), today())
    if errs:
        raise _bad(errs)
    new_owners = owners
    if body.owners is not None and sorted(set(body.owners)) != owners:
        if not authz.can_assign(user):
            raise authz.forbidden("เฉพาะ ผอ.คบ. เท่านั้นที่เปลี่ยนผู้รับผิดชอบ")
        new_owners = lgl_owners(body.owners)
    if v["classification"] == "restricted" and not new_owners:
        raise _bad(["คดีชั้นจำกัดเฉพาะผู้รับผิดชอบต้องมีผู้รับผิดชอบอย่างน้อย 1 คน"])
    dup = find_case(conn, v["court_name"], v["black_no"])
    if dup and dup["case_id"] != case_id:
        raise HTTPException(409, detail={"code": "DUPLICATE", "detail": f"มีคดีดำที่ {v['black_no']} ของ{v['court_name']} แล้ว"})
    if all(v[f] == k[f] for f in FIELDS) and new_owners == owners:
        return {"case_id": case_id, "changed": []}
    before = snapshot(conn, case_id)
    update_case(conn, case_id, v, user.user_id)
    if new_owners != owners:
        set_owners(conn, case_id, new_owners)
    summary = add_revision(conn, case_id, "edit", user.user_id, before)
    changed = [f["field"] for f in summary["fields"]]
    audit.record(conn, user, "court_case.edit", f"court_case/{case_id}", detail={"fields": changed})
    reindex_cases(conn, only=("court_case", case_id))
    return {"case_id": case_id, "changed": changed}


@router.get("/{case_id}/history")
def history(case_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _load(conn, user, case_id)
    items = rows(conn, """SELECT rev_no, kind, actor, summary, created_at FROM court_case_revision
                          WHERE case_id = :i ORDER BY rev_no DESC""", i=case_id)
    for r in items:
        r["created_at"] = r["created_at"].isoformat()
    return {"items": items}


# ---------------------------------------------------------------- API: กระบวนพิจารณา
class ProceedingIn(BaseModel):
    proceeding_date: date
    title: str = Field(min_length=2, max_length=300)
    detail: str | None = Field(default=None, max_length=2000)


@router.post("/{case_id}/proceedings", status_code=201)
def add_proceeding(case_id: int, body: ProceedingIn, user: User = Depends(current_user),
                   conn: Connection = Depends(get_conn)):
    _load(conn, user, case_id, edit=True)
    before = snapshot(conn, case_id)
    pid = conn.execute(text("""INSERT INTO court_proceeding (case_id, proceeding_date, title, detail)
                               VALUES (:c, :d, :t, :x) RETURNING proceeding_id"""),
                       {"c": case_id, "d": body.proceeding_date, "t": body.title.strip(),
                        "x": (body.detail or "").strip() or None}).scalar()
    add_revision(conn, case_id, "proceeding", user.user_id, before)
    audit.record(conn, user, "court_case.proceeding.add", f"court_case/{case_id}",
                 detail={"proceeding_id": pid, "date": body.proceeding_date.isoformat(), "title": body.title.strip()})
    return {"proceeding_id": pid}


@router.delete("/{case_id}/proceedings/{proceeding_id}")
def delete_proceeding(case_id: int, proceeding_id: int, user: User = Depends(current_user),
                      conn: Connection = Depends(get_conn)):
    _load(conn, user, case_id, edit=True)
    p = one(conn, "SELECT * FROM court_proceeding WHERE proceeding_id = :p AND case_id = :c", p=proceeding_id, c=case_id)
    if not p:
        raise authz.not_found()
    before = snapshot(conn, case_id)
    conn.execute(text("DELETE FROM court_proceeding WHERE proceeding_id = :p"), {"p": proceeding_id})
    add_revision(conn, case_id, "proceeding", user.user_id, before)
    audit.record(conn, user, "court_case.proceeding.delete", f"court_case/{case_id}",
                 detail={"date": p["proceeding_date"].isoformat(), "title": p["title"]})
    return {"deleted": proceeding_id}


# ---------------------------------------------------------------- API: เอกสารในสำนวน
def process_document(document_id: int) -> None:
    """ถอดข้อความจากไฟล์ (Word / PDF / OCR ในเครื่อง) แล้ว index ให้ค้นได้"""
    from ..law.uploads import extract
    eng = get_engine()
    with eng.begin() as c:
        d = one(c, """SELECT d.*, f.stored_path FROM case_document d JOIN raw_file f USING (sha256)
                      WHERE d.document_id = :i""", i=document_id)
    try:
        paras, method, _ = extract(str(RawStore().path(d["stored_path"])), Path(d["original_name"]).suffix.lower())
        body = "\n".join(p for p in paras if p.strip())
        if not body.strip():
            raise ValueError("ไม่พบข้อความในไฟล์")
        with eng.begin() as c:
            c.execute(text("""UPDATE case_document SET excerpt = :x, status = 'ready', method = :m, error = NULL
                              WHERE document_id = :i"""), {"x": body, "m": method.split(":")[0], "i": document_id})
            reindex_cases(c, only=("court_case", d["owner_id"]))
    except Exception as e:
        log.warning("case document %s failed: %s", document_id, e)
        with eng.begin() as c:
            c.execute(text("UPDATE case_document SET status = 'failed', error = :e WHERE document_id = :i"),
                      {"e": str(e)[:500], "i": document_id})


def process_document_in_background(document_id: int) -> None:
    threading.Thread(target=process_document, args=(document_id,), daemon=True, name=f"case-doc-{document_id}").start()


@router.post("/{case_id}/documents", status_code=201)
async def add_document(case_id: int, file: UploadFile = File(...), doc_type: str = Form(...),
                       doc_no: str | None = Form(None), doc_date: date | None = Form(None),
                       user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _load(conn, user, case_id, edit=True)
    if public_data_only():
        raise HTTPException(400, detail={"code": "PUBLIC_DATA_ONLY",
                                         "detail": "เครื่องนี้เก็บเฉพาะข้อมูลสาธารณะ — แนบเอกสารคดีบนเครื่องของสำนักงาน"})
    if doc_type not in DOC_TYPES:
        raise _bad([f"ประเภทเอกสารต้องเป็น {', '.join(DOC_TYPES)}"])
    if doc_no and len(doc_no) > 40:
        raise _bad(["เลขที่เอกสารยาวเกิน 40 อักษร"])
    if doc_date and doc_date > today():
        raise _bad(["วันที่เอกสารเป็นวันในอนาคต"])
    name = Path(file.filename or "file").name
    ext = Path(name).suffix.lower()
    if ext not in DOC_EXTS:
        raise _bad(["รองรับเฉพาะไฟล์ .pdf .docx .doc"])
    data = await file.read(MAX_DOC_BYTES + 1)
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(413, detail={"code": "TOO_LARGE", "detail": "ไฟล์ใหญ่เกิน 50 MB"})
    from .. import filecheck
    try:
        fc = filecheck.check(data, ext)
    except filecheck.FileRejected as e:
        with get_engine().begin() as c3:
            audit.record(c3, user, "court_case.document.reject_file", f"court_case/{case_id}", outcome="DENY",
                         detail={"name": name, "reason": str(e), "signature": e.signature, "bytes": len(data)})
        raise HTTPException(e.status, detail={"code": "FILE_REJECTED", "detail": str(e)}) from e
    from ..seed.raw_files import add_file
    with tempfile.TemporaryDirectory() as td, get_engine().begin() as c2:  # commit ก่อนเริ่มงานเบื้องหลัง
        tmp = Path(td) / f"doc{ext}"
        tmp.write_bytes(data)
        st = add_file(c2, tmp, "internal", source=f"เอกสารคดี (court_case/{case_id})", received_from=user.user_id,
                      original_name=name, added_by=user.user_id)
        pages = c2.execute(text("SELECT pages FROM raw_file WHERE sha256 = :s"), {"s": st["sha256"]}).scalar()
        did = c2.execute(text("""
            INSERT INTO case_document (owner_type, owner_id, doc_no, doc_type, doc_date, pages, excerpt, sha256,
                                       original_name, status, added_by, added_at)
            VALUES ('court_case', :c, :no, :t, :d, :p, '', :s, :n, 'processing', :u, now()) RETURNING document_id"""),
            {"c": case_id, "no": (doc_no or "").strip() or None, "t": doc_type, "d": doc_date, "p": pages,
             "s": st["sha256"], "n": name, "u": user.user_id}).scalar()
    audit.record(conn, user, "court_case.document.add", f"court_case/{case_id}",
                 detail={"document_id": did, "name": name, "sha256": st["sha256"], "doc_type": doc_type,
                         "av": fc.av, "pages": fc.pages})
    process_document_in_background(did)
    return {"document_id": did, "status": "processing"}


def _doc(conn: Connection, case_id: int, document_id: int) -> dict:
    d = one(conn, """SELECT * FROM case_document WHERE document_id = :d AND owner_type = 'court_case' AND owner_id = :c""",
            d=document_id, c=case_id)
    if not d:
        raise authz.not_found()
    return d


@router.get("/{case_id}/documents/{document_id}/file")
def open_document(case_id: int, document_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """เปิดไฟล์ต้นฉบับผ่านสิทธิ์ของคดี (คดีชั้นจำกัดเปิดได้เฉพาะผู้รับผิดชอบ)"""
    _load(conn, user, case_id)
    d = _doc(conn, case_id, document_id)
    f = one(conn, "SELECT stored_path, mime, original_name FROM raw_file WHERE sha256 = :s", s=d["sha256"]) if d["sha256"] else None
    path = RawStore().path(f["stored_path"]) if f else None
    if not path or not path.exists():
        raise authz.not_found()
    audit.record(conn, user, "court_case.document.view", f"court_case/{case_id}",
                 detail={"document_id": document_id, "name": f["original_name"]})
    return FileResponse(path, media_type=f["mime"],
                        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(f['original_name'])}"})


@router.post("/{case_id}/documents/{document_id}/reprocess")
def reprocess_document(case_id: int, document_id: int, user: User = Depends(current_user),
                       conn: Connection = Depends(get_conn)):
    _load(conn, user, case_id, edit=True)
    d = _doc(conn, case_id, document_id)
    if not d["sha256"] or d["status"] == "processing":
        raise HTTPException(409, detail={"code": "CONFLICT", "detail": "ถอดข้อความใหม่ไม่ได้"})
    with get_engine().begin() as c2:
        c2.execute(text("UPDATE case_document SET status = 'processing', error = NULL WHERE document_id = :i"),
                   {"i": document_id})
    audit.record(conn, user, "court_case.document.reprocess", f"court_case/{case_id}", detail={"document_id": document_id})
    process_document_in_background(document_id)
    return {"document_id": document_id, "status": "processing"}


@router.delete("/{case_id}/documents/{document_id}")
def delete_document(case_id: int, document_id: int, user: User = Depends(current_user),
                    conn: Connection = Depends(get_conn)):
    """เอาเอกสารออกจากสำนวน (ไฟล์ต้นฉบับยังอยู่ในที่เก็บไฟล์ตามหลักไม่ลบหลักฐาน)"""
    _load(conn, user, case_id, edit=True)
    d = _doc(conn, case_id, document_id)
    if d["status"] == "processing":
        raise HTTPException(409, detail={"code": "CONFLICT", "detail": "กำลังถอดข้อความ — รอให้เสร็จก่อน"})
    conn.execute(text("DELETE FROM case_document WHERE document_id = :d"), {"d": document_id})
    audit.record(conn, user, "court_case.document.delete", f"court_case/{case_id}",
                 detail={"document_id": document_id, "doc_type": d["doc_type"], "name": d["original_name"],
                         "sha256": d["sha256"]})
    reindex_cases(conn, only=("court_case", case_id))
    return {"deleted": document_id}


def mark_interrupted(conn: Connection) -> None:
    conn.execute(text("""UPDATE case_document SET status = 'failed', error = 'ถูกขัดจังหวะ (API restart) — กดถอดใหม่'
                         WHERE status = 'processing'"""))
