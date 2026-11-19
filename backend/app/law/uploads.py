"""นำเข้าเอกสารกฎหมาย (PDF / Word) จากหน้าเว็บ — นิติกร (LGL) และผู้ดูแล (ADM)

อัปโหลด → ระบบถอดข้อความ (Word, PDF มีชั้นข้อความ หรือ OCR ในเครื่อง) และแยกเป็นข้อ/มาตรา → ร่าง
→ ผู้อัปโหลดตรวจทาน/แก้ → ผู้อนุมัติ (LGL/ADM ที่ไม่ใช่ผู้อัปโหลด) อนุมัติ → เผยแพร่ในระบบค้นและผู้ช่วย AI
ร่างและไฟล์ต้นฉบับไม่ถูกล้างตอน seed; ฉบับที่อนุมัติแล้วถูกนำเข้าซ้ำจากร่างทุกครั้งที่ seed (load_approved)
"""
import json
import logging
import re
import subprocess
import tempfile
import threading
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import audit
from ..auth import User, current_user
from ..db import get_conn, get_engine, rows
from ..storage import RawStore
from . import docparse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/laws/uploads", tags=["law-uploads"])

ROLES = ("LGL", "ADM")
EXTS = (".pdf", ".docx", ".doc")
MAX_BYTES = 50 * 1024 * 1024
DOC_TYPES = ("ประกาศ", "ระเบียบ", "ข้อบังคับ", "คำสั่ง", "พระราชกฤษฎีกา", "พระราชบัญญัติ", "กฎกระทรวง")
SOURCE = "ไฟล์นำเข้าจากหน้าเว็บ สำนักงาน กกพ."
_ocr_slot = threading.Semaphore(1)  # OCR ใช้ CPU มาก — ทำทีละไฟล์


def require_importer(user: User) -> None:
    if user.role not in ROLES:
        raise HTTPException(404, "ไม่พบหน้า")  # ไม่เปิดเผยว่ามีฟังก์ชันนี้


def _get(conn: Connection, upload_id: int) -> dict:
    r = rows(conn, "SELECT * FROM law_upload WHERE upload_id = :i", i=upload_id)
    if not r:
        raise HTTPException(404, "ไม่พบรายการนำเข้า")
    return r[0]


def _view(u: dict, full: bool = False) -> dict:
    d = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in u.items() if k != "draft"}
    dr = u.get("draft") or {}
    d["title"] = dr.get("title")
    d["eli_id"] = dr.get("id")
    d["provision_count"] = len(dr.get("provisions") or [])
    d["notes"] = dr.get("importNotes") or []
    if full:
        d["draft"] = dr
    return d


# ---------------------------------------------------------------- extract + parse
def _pdf_text(path: str) -> str:
    return subprocess.run(["pdftotext", "-q", "-layout", path, "-"], capture_output=True, text=True, timeout=300).stdout


def extract(path: str, ext: str, force_ocr: bool = False) -> tuple[list[str], str, str]:
    """→ (ย่อหน้า, วิธีที่ใช้, ข้อความดิบสำหรับหาหัวราชกิจจาฯ)"""
    if ext == ".docx":
        ps = docparse.docx_paragraphs(path)
        return ps, "docx", "\n".join(ps)
    if ext == ".doc":
        ps = docparse.doc_paragraphs(path)
        if not ps:
            raise ValueError("แปลงไฟล์ .doc ไม่ได้บนเซิร์ฟเวอร์นี้ — บันทึกเป็น .docx หรือ PDF แล้วนำเข้าใหม่")
        return ps, "doc", "\n".join(ps)
    raw = "" if force_ocr else _pdf_text(path)
    ps = docparse.layout_paragraphs(raw) if raw.strip() else []
    thai = len(docparse.THAI_RE.findall("\n".join(ps)))
    if force_ocr or thai < 300 or docparse.tone_ratio("\n".join(ps)) < 0.06:
        from .ocr import clean, engine_name, ocr_pdf
        with _ocr_slot:
            raw = clean(ocr_pdf(path))   # OCR_ENGINE: tesseract | akson | openthai
        method = f"ocr:{engine_name()}"
        ps = docparse.layout_paragraphs(raw)
        if len(docparse.THAI_RE.findall(raw)) < 100:
            raise ValueError("ถอดข้อความจากไฟล์ไม่ได้ (OCR อ่านภาษาไทยได้น้อยเกินไป) — ตรวจว่าไฟล์ชัดและไม่กลับหัว")
        return ps, method, raw
    return ps, "pdf", raw


def build_draft(u: dict, path: str) -> dict:
    paras, method, raw = extract(path, u["ext"], u["force_ocr"])
    paras = docparse.fix_lost_am("\n".join(paras)).split("\n")
    gaz, pub = docparse.gazette_from_text(raw)
    stem = re.sub(r"^\d+\s*\.\s*", "", Path(u["original_name"]).stem).strip()
    reg_title = u["register_title"] or stem
    args = dict(doc_type=u["doc_type"], reg_title=reg_title, key=f"u{u['upload_id']}", gaz=gaz, pub=pub,
                ocr=method.startswith("ocr"), ext=u["ext"], short=stem[:100], source_default=SOURCE)
    eff = u["effective_date"].isoformat() if u["effective_date"] else None
    inst, why, _ = docparse.build_instrument(paras, effective_date=eff, **args)
    if inst is None and why == "ไม่พบวันที่ใช้บังคับ":
        # ยังสร้างร่างได้ — ให้ผู้ตรวจทานระบุวันใช้บังคับก่อนอนุมัติ
        inst, why, _ = docparse.build_instrument(paras, effective_date=date.today().isoformat(), **args)
        if inst is not None:
            inst["effectiveDate"] = None
            for p in inst["provisions"]:
                p["versions"][0]["validFrom"] = None
            inst["importNotes"] = [n for n in inst["importNotes"] if not n.startswith("วันใช้บังคับ")]
            inst["importNotes"].insert(0, "ไม่พบวันใช้บังคับในเอกสาร — ระบุก่อนอนุมัติ")
            eff = None
    if inst is None:
        raise ValueError(f"แปลงเป็นตัวบทไม่ได้: {why}")
    if eff and u["effective_date"]:
        inst["importNotes"] = [n for n in inst["importNotes"] if not n.startswith("วันใช้บังคับไม่มีในทะเบียน")]
        inst["importNotes"].insert(0, "วันใช้บังคับตามที่ผู้นำเข้าระบุ")
    inst["registerTitle"] = u["register_title"]  # ผูกทะเบียนเฉพาะเมื่อเลือกแถวทะเบียน
    if u["classification"] == "internal":
        inst["classification"] = "internal"
    if method.startswith("ocr:"):
        inst["importNotes"].append(f"OCR engine: {method[4:]}")
    inst["sourceFiles"] = [{"sha256": u["sha256"], "name": u["original_name"], "role": "text"}]
    inst["importNotes"].append(f"นำเข้าจากหน้าเว็บโดย {u['uploaded_by']} (รายการที่ {u['upload_id']})")
    return {"draft": inst, "method": method}


def process(upload_id: int) -> None:
    eng = get_engine()
    with eng.begin() as c:
        u = _get(c, upload_id)
        f = rows(c, "SELECT stored_path FROM raw_file WHERE sha256 = :s", s=u["sha256"])[0]
    try:
        res = build_draft(u, str(RawStore().path(f["stored_path"])))
        with eng.begin() as c:
            c.execute(text("""UPDATE law_upload SET status = 'draft', method = :m, draft = CAST(:d AS jsonb), error = NULL
                              WHERE upload_id = :i"""),
                      {"m": res["method"].split(":")[0], "d": json.dumps(res["draft"], ensure_ascii=False), "i": upload_id})
    except Exception as e:  # noqa: BLE001 — แสดงสาเหตุให้ผู้นำเข้าเห็น
        log.warning("law upload %s failed: %s", upload_id, e)
        with eng.begin() as c:
            c.execute(text("UPDATE law_upload SET status = 'failed', error = :e WHERE upload_id = :i"),
                      {"e": str(e)[:500], "i": upload_id})


def process_in_background(upload_id: int) -> None:
    threading.Thread(target=process, args=(upload_id,), daemon=True, name=f"law-upload-{upload_id}").start()


# ---------------------------------------------------------------- publish
def _publish(conn: Connection, inst: dict, actor: str, src: str) -> None:
    from ..seed.seed import load_laws
    load_laws(conn, [inst])
    conn.execute(text("""
        INSERT INTO law_import_log (kind, source_name, source_sha256, instrument_eli, title_th, provisions, versions,
                                    actor, detail)
        VALUES ('web_upload', :src, :sha, :eli, :title, :np, :nv, :actor, CAST(:detail AS jsonb))"""), {
        "src": src, "sha": inst["sourceFiles"][0]["sha256"], "eli": inst["id"], "title": inst["title"],
        "np": len(inst["provisions"]), "nv": sum(len(p["versions"]) for p in inst["provisions"]), "actor": actor,
        "detail": json.dumps({"origin": inst.get("source"), "source_files": inst["sourceFiles"]}, ensure_ascii=False)})
    for note in inst.get("importNotes", []):
        conn.execute(text("INSERT INTO law_open_issue (instrument_eli, kind, note) VALUES (:i, 'import_note', :n)"),
                     {"i": inst["id"], "n": note})
    for f in inst["sourceFiles"]:
        conn.execute(text("""INSERT INTO raw_file_link (sha256, instrument_eli, role) VALUES (:s, :e, :r)
                             ON CONFLICT DO NOTHING"""), {"s": f["sha256"], "e": inst["id"], "r": f["role"]})


def load_approved(conn: Connection) -> int:
    """seed: นำเข้าฉบับที่อนุมัติจากหน้าเว็บซ้ำ (ข้าม ELI ที่ไฟล์ seed มีแล้ว)"""
    try:
        with conn.begin_nested():
            ups = rows(conn, "SELECT * FROM law_upload WHERE status = 'approved' ORDER BY upload_id")
    except Exception:  # noqa: BLE001 — ฐานข้อมูลเก่าที่ยังไม่มีตาราง
        return 0
    have = {r[0] for r in conn.execute(text("SELECT eli_id FROM legal_instrument"))}
    n = 0
    for u in ups:
        if u["draft"]["id"] in have:
            continue
        _publish(conn, u["draft"], u["reviewed_by"] or "seed", f"web/upload-{u['upload_id']}")
        n += 1
    return n


def _after_publish_embedding() -> None:
    """อัปเดต embedding index ที่ active หลังข้อมูล commit แล้ว (หน่วงสั้น ๆ ให้ transaction ของคำขอจบก่อน)"""
    def run():
        from ..search import vector_index
        with get_engine().begin() as c:
            idx = vector_index.active_index(c)
        if idx:
            vector_index.build(idx["index_id"])
    threading.Timer(3.0, run).start()


# ---------------------------------------------------------------- API
@router.get("")
def list_uploads(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    items = [_view(u) for u in rows(conn, "SELECT * FROM law_upload ORDER BY upload_id DESC LIMIT 200")]
    return {"items": items, "doc_types": list(DOC_TYPES), "me": user.user_id}


@router.get("/register-options")
def register_options(q: str = "", user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """แถวทะเบียนที่ยังไม่มีตัวบท — ให้ผู้นำเข้าเลือกผูก (ระบบใช้วันใช้บังคับจากทะเบียน)"""
    require_importer(user)
    return {"items": rows(conn, """
        SELECT register_id, reg_no, doc_type, title_th, effective_date, status FROM law_register
        WHERE status <> 'GROUP' AND instrument_eli IS NULL AND (:q = '' OR title_th ILIKE :p OR reg_no = :q)
        ORDER BY (status = 'IN_FORCE') DESC, sheet_row LIMIT 30""", q=q.strip(), p=f"%{q.strip()}%")}


@router.post("")
async def upload(file: UploadFile = File(...), doc_type: str = Form(...), classification: str = Form("public"),
                 register_id: int | None = Form(None), effective_date: date | None = Form(None),
                 force_ocr: bool = Form(False), user: User = Depends(current_user),
                 conn: Connection = Depends(get_conn)):
    require_importer(user)
    name = Path(file.filename or "file").name
    ext = Path(name).suffix.lower()
    if ext not in EXTS:
        raise HTTPException(400, "รองรับเฉพาะไฟล์ .pdf .docx .doc")
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, f"ประเภทเอกสารต้องเป็น {', '.join(DOC_TYPES)}")
    if classification not in ("public", "internal"):
        raise HTTPException(400, "ชั้นข้อมูลต้องเป็น public หรือ internal")
    reg_title = None
    if register_id is not None:
        reg = rows(conn, "SELECT title_th, effective_date, instrument_eli FROM law_register WHERE register_id = :r",
                   r=register_id)
        if not reg:
            raise HTTPException(400, "ไม่พบแถวทะเบียน")
        if reg[0]["instrument_eli"]:
            raise HTTPException(409, f"แถวทะเบียนนี้มีตัวบทแล้ว ({reg[0]['instrument_eli']})")
        reg_title = reg[0]["title_th"]
        effective_date = effective_date or reg[0]["effective_date"]
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "ไฟล์ใหญ่เกิน 50 MB")
    head = data[:8]
    if (ext == ".pdf" and not head.startswith(b"%PDF")) or (ext == ".docx" and not head.startswith(b"PK")) \
            or (ext == ".doc" and not head.startswith(b"\xd0\xcf\x11\xe0")):
        raise HTTPException(400, "เนื้อไฟล์ไม่ตรงกับนามสกุล")
    from ..seed.raw_files import add_file
    with tempfile.TemporaryDirectory() as td, get_engine().begin() as c2:  # commit ก่อนเริ่มงานเบื้องหลัง
        tmp = Path(td) / f"upload{ext}"
        tmp.write_bytes(data)
        st = add_file(c2, tmp, classification, source="นำเข้าจากหน้าเว็บ", received_from=user.user_id,
                      original_name=name, added_by=user.user_id)
        uid = c2.execute(text("""
            INSERT INTO law_upload (sha256, original_name, ext, classification, doc_type, register_id, register_title,
                                    effective_date, force_ocr, uploaded_by)
            VALUES (:s, :n, :e, :c, :t, :r, :rt, :d, :o, :u) RETURNING upload_id"""),
            {"s": st["sha256"], "n": name, "e": ext, "c": classification, "t": doc_type, "r": register_id,
             "rt": reg_title, "d": effective_date, "o": force_ocr, "u": user.user_id}).scalar()
    audit.record(conn, user, "law.upload.create", f"law_upload/{uid}",
                 detail={"name": name, "sha256": st["sha256"], "classification": classification, "register_id": register_id})
    process_in_background(uid)
    return _view(_get(conn, uid))


@router.get("/{upload_id}")
def get_upload(upload_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    return _view(_get(conn, upload_id), full=True)


class ProvisionEdit(BaseModel):
    number: str = Field(max_length=40)
    part: str | None = Field(default=None, max_length=300)
    text: str = Field(max_length=60000)


class DraftEdit(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    code: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=60)
    classification: str | None = None
    effectiveDate: date | None = None
    publishedDate: date | None = None
    source: str | None = Field(default=None, max_length=300)
    provisions: list[ProvisionEdit] | None = None


def _prov_id(eli: str, number: str, used: set) -> str:
    n = docparse.th2ar(number.strip())
    m = re.match(r"^(ข้อ|มาตรา)\s*(\d+(?:/\d+)?)", n)
    pid = (f"{eli}/{'sec' if m.group(1) == 'มาตรา' else 'clause'}-{m.group(2).replace('/', '-')}" if m
           else f"{eli}/item-annex" if n == "แนบท้าย" else f"{eli}/item-1")
    base, k = pid, 2
    while pid in used:
        pid, k = f"{base}-{k}", k + 1
    used.add(pid)
    return pid


@router.put("/{upload_id}")
def edit_draft(upload_id: int, body: DraftEdit, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] != "draft":
        raise HTTPException(409, "แก้ไขได้เฉพาะรายการที่เป็นร่าง")
    d = dict(u["draft"])
    for k in ("title", "code", "category", "source"):
        if getattr(body, k) is not None:
            d[k] = getattr(body, k).strip()
    if body.classification is not None:
        if body.classification not in ("public", "internal"):
            raise HTTPException(400, "ชั้นข้อมูลต้องเป็น public หรือ internal")
        d["classification"] = body.classification
    if body.publishedDate is not None:
        d["publishedDate"] = body.publishedDate.isoformat()
    if body.effectiveDate is not None:
        d["effectiveDate"] = body.effectiveDate.isoformat()
        d["importNotes"] = [n for n in d.get("importNotes", []) if not n.startswith("ไม่พบวันใช้บังคับในเอกสาร")]
        d["importNotes"].insert(0, "วันใช้บังคับตามที่ผู้ตรวจทานระบุ")
    if body.provisions is not None:
        if not body.provisions:
            raise HTTPException(400, "ต้องมีอย่างน้อย 1 บทบัญญัติ")
        old = {p["number"]: p for p in d["provisions"]}
        used: set = set()
        amend = d["provisions"][0]["versions"][0]["amendingInstrument"] if d["provisions"] else f"ตัวบทตั้งต้น ({SOURCE})"
        d["provisions"] = [{"id": _prov_id(d["id"], p.number, used), "number": p.number.strip(), "part": p.part or None,
                            "sourcePage": None,
                            "versions": [{"version": 1, "validFrom": d["effectiveDate"], "validTo": None,
                                          "text": p.text.strip(), "summary": None, "relations": [],
                                          "amendingInstrument": (old.get(p.number) or {}).get("versions", [{}])[0]
                                          .get("amendingInstrument", amend)}]}
                           for p in body.provisions if p.text.strip()]
    for p in d["provisions"]:  # วันใช้บังคับเปลี่ยน → ทุกบทบัญญัติเริ่มมีผลวันเดียวกัน
        p["versions"][0]["validFrom"] = d["effectiveDate"]
    conn.execute(text("""UPDATE law_upload SET draft = CAST(:d AS jsonb), edited_by = :u, edited_at = now()
                         WHERE upload_id = :i"""), {"d": json.dumps(d, ensure_ascii=False), "u": user.user_id, "i": upload_id})
    audit.record(conn, user, "law.upload.edit", f"law_upload/{upload_id}",
                 detail={"fields": sorted(k for k, v in body.model_dump(exclude_none=True).items())})
    return _view(_get(conn, upload_id), full=True)


class Review(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/{upload_id}/approve")
def approve(upload_id: int, body: Review, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] != "draft":
        raise HTTPException(409, "อนุมัติได้เฉพาะรายการที่เป็นร่าง")
    if u["uploaded_by"] == user.user_id:
        raise HTTPException(403, "ผู้อนุมัติต้องไม่ใช่ผู้นำเข้า (หลักสี่ตา)")
    inst = u["draft"]
    if not inst.get("effectiveDate"):
        raise HTTPException(400, "ระบุวันใช้บังคับก่อนอนุมัติ")
    if conn.execute(text("SELECT 1 FROM legal_instrument WHERE eli_id = :e"), {"e": inst["id"]}).scalar():
        raise HTTPException(409, f"มีตราสาร {inst['id']} ในระบบแล้ว")
    from ..search.indexer import reindex_laws, reindex_register
    from . import relations
    _publish(conn, inst, user.user_id, f"web/upload-{upload_id}")
    if u["register_id"] is not None:
        conn.execute(text("""UPDATE law_register SET instrument_eli = :e
                             WHERE register_id = :r AND instrument_eli IS NULL"""), {"e": inst["id"], "r": u["register_id"]})
        reindex_register(conn)
    relations.rebuild(conn)
    reindex_laws(conn, only_eli=inst["id"])
    conn.execute(text("""UPDATE law_upload SET status = 'approved', reviewed_by = :u, reviewed_at = now(), review_note = :n
                         WHERE upload_id = :i"""), {"u": user.user_id, "n": body.note, "i": upload_id})
    audit.record(conn, user, "law.upload.approve", f"law_upload/{upload_id}",
                 detail={"eli": inst["id"], "provisions": len(inst["provisions"]), "uploaded_by": u["uploaded_by"]})
    _after_publish_embedding()
    return _view(_get(conn, upload_id), full=True)


@router.post("/{upload_id}/reject")
def reject(upload_id: int, body: Review, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] not in ("draft", "failed"):
        raise HTTPException(409, "ส่งกลับได้เฉพาะรายการที่เป็นร่าง")
    conn.execute(text("""UPDATE law_upload SET status = 'rejected', reviewed_by = :u, reviewed_at = now(), review_note = :n
                         WHERE upload_id = :i"""), {"u": user.user_id, "n": body.note, "i": upload_id})
    audit.record(conn, user, "law.upload.reject", f"law_upload/{upload_id}", detail={"note": body.note})
    return _view(_get(conn, upload_id), full=True)


@router.post("/{upload_id}/reprocess")
def reprocess(upload_id: int, force_ocr: bool = False, user: User = Depends(current_user),
              conn: Connection = Depends(get_conn)):
    """ถอดข้อความใหม่ (เช่น บังคับ OCR) — ทับร่างเดิม"""
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] not in ("draft", "failed", "rejected"):
        raise HTTPException(409, "ถอดใหม่ได้เฉพาะรายการที่ยังไม่อนุมัติ")
    with get_engine().begin() as c2:
        c2.execute(text("""UPDATE law_upload SET status = 'processing', force_ocr = :o, error = NULL, draft = NULL,
                           reviewed_by = NULL, reviewed_at = NULL, review_note = NULL WHERE upload_id = :i"""),
                   {"o": force_ocr or u["force_ocr"], "i": upload_id})
    audit.record(conn, user, "law.upload.reprocess", f"law_upload/{upload_id}", detail={"force_ocr": force_ocr})
    process_in_background(upload_id)
    return _view(_get(conn, upload_id))


@router.delete("/{upload_id}")
def delete_upload(upload_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ลบรายการที่ยังไม่อนุมัติ (ไฟล์ต้นฉบับยังเก็บในที่เก็บไฟล์ตามหลักไม่ลบหลักฐาน)"""
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] == "approved":
        raise HTTPException(409, "ลบรายการที่อนุมัติแล้วไม่ได้")
    if u["status"] == "processing":
        raise HTTPException(409, "กำลังถอดข้อความ — รอให้เสร็จก่อน")
    if user.user_id != u["uploaded_by"] and user.role != "ADM":
        raise HTTPException(403, "ลบได้เฉพาะผู้นำเข้าหรือผู้ดูแล")
    conn.execute(text("DELETE FROM law_upload WHERE upload_id = :i"), {"i": upload_id})
    audit.record(conn, user, "law.upload.delete", f"law_upload/{upload_id}", detail={"name": u["original_name"]})
    return {"deleted": upload_id}


def mark_interrupted(conn: Connection) -> None:
    conn.execute(text("""UPDATE law_upload SET status = 'failed', error = 'ถูกขัดจังหวะ (API restart) — กดถอดใหม่'
                         WHERE status = 'processing'"""))
