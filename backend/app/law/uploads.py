"""นำเข้าเอกสารกฎหมาย (PDF / Word) จากหน้าเว็บ — นิติกร (LGL) และผู้ดูแล (ADM)

อัปโหลด → ระบบถอดข้อความ (Word, PDF มีชั้นข้อความ หรือ OCR ในเครื่อง) และแยกเป็นข้อ/มาตรา → ร่าง
→ ตรวจทาน/แก้ (บันทึกทุกรุ่น) → ส่งตรวจ → ผู้อนุมัติอนุมัติ → เผยแพร่ในระบบค้นและผู้ช่วย AI
ผู้อนุมัติต้องไม่ใช่ผู้นำเข้า ผู้แก้ร่าง หรือผู้ส่งตรวจ (หลักสี่ตา); ผู้อนุมัติส่งกลับให้แก้ได้
ฉบับที่เผยแพร่แล้วแก้ผ่าน "คำขอแก้ไข" ซึ่งวนขั้นตอนเดียวกัน แล้วแทนที่ตัวบทเดิม (ฉบับเดิมเป็น superseded)
ร่าง ประวัติร่าง และไฟล์ต้นฉบับไม่ถูกล้างตอน seed; ฉบับที่อนุมัติแล้วถูกนำเข้าซ้ำจากร่างทุกครั้งที่ seed (load_approved)
"""
import difflib
import json
import logging
import re
import tempfile
import threading
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import audit, clock
from ..auth import User, current_user
from ..db import get_conn, get_engine, rows
from ..storage import RawStore
from ..config import Q_MAX
from . import docparse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/laws/uploads", tags=["law-uploads"])

ROLES = ("LGL", "ADM")
EXTS = (".pdf", ".docx", ".doc")
MAX_BYTES = 50 * 1024 * 1024
DOC_TYPES = ("ประกาศ", "ระเบียบ", "ข้อบังคับ", "คำสั่ง", "พระราชกฤษฎีกา", "พระราชบัญญัติ", "กฎกระทรวง")
SOURCE = "ไฟล์นำเข้าจากหน้าเว็บ สำนักงาน กกพ."
_ocr_slot = threading.Semaphore(1)  # OCR ใช้ CPU มาก — ทำทีละไฟล์
INST_FIELDS = ("title", "code", "category", "classification", "effectiveDate", "publishedDate", "source")
BASE_KINDS = ("extract", "reprocess", "correction")   # รุ่นตั้งต้นที่ใช้เทียบกับร่างล่าสุด


def require_importer(user: User) -> None:
    if user.role not in ROLES:
        raise HTTPException(404, "ไม่พบหน้า")  # ไม่เปิดเผยว่ามีฟังก์ชันนี้


def _get(conn: Connection, upload_id: int) -> dict:
    r = rows(conn, "SELECT * FROM law_upload WHERE upload_id = :i", i=upload_id)
    if not r:
        raise HTTPException(404, "ไม่พบรายการนำเข้า")
    return r[0]


def _editors(conn: Connection, ids: list[int]) -> dict[int, list[str]]:
    if not ids:
        return {}
    return {r["upload_id"]: r["editors"] for r in rows(conn, """
        SELECT upload_id, array_agg(DISTINCT actor ORDER BY actor) AS editors FROM law_upload_revision
        WHERE kind = 'edit' AND upload_id = ANY(:ids) GROUP BY upload_id""", ids=ids)}


def participants(u: dict, editors: list[str]) -> set[str]:
    """ผู้ที่มีส่วนทำร่าง — อนุมัติรายการนี้ไม่ได้ (หลักสี่ตา)"""
    return {x for x in (u["uploaded_by"], u.get("submitted_by"), *editors) if x}


def _view(u: dict, full: bool = False, me: User | None = None, editors: list[str] | None = None) -> dict:
    d = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in u.items() if k != "draft"}
    dr = u.get("draft") or {}
    d["title"] = dr.get("title")
    d["eli_id"] = dr.get("id")
    d["provision_count"] = len(dr.get("provisions") or [])
    d["notes"] = dr.get("importNotes") or []
    if editors is not None:
        d["editors"] = editors
        d["can_approve"] = bool(me and u["status"] == "submitted" and me.user_id not in participants(u, editors))
    if full:
        d["draft"] = dr
    return d


def _full(conn: Connection, upload_id: int, me: User) -> dict:
    u = _get(conn, upload_id)
    return _view(u, full=True, me=me, editors=_editors(conn, [upload_id]).get(upload_id, []))


# ---------------------------------------------------------------- revisions + diff
def add_revision(conn: Connection, upload_id: int, kind: str, draft: dict, actor: str, summary: dict | None = None) -> int:
    n = conn.execute(text("SELECT coalesce(max(rev_no), 0) + 1 FROM law_upload_revision WHERE upload_id = :i"),
                     {"i": upload_id}).scalar()
    conn.execute(text("""INSERT INTO law_upload_revision (upload_id, rev_no, kind, draft, actor, summary)
                         VALUES (:i, :n, :k, CAST(:d AS jsonb), :a, CAST(:s AS jsonb))"""),
                 {"i": upload_id, "n": n, "k": kind, "d": json.dumps(draft, ensure_ascii=False), "a": actor,
                  "s": json.dumps(summary or {}, ensure_ascii=False)})
    return n


def _keyed(d: dict) -> dict[str, dict]:
    """บทบัญญัติตามเลขข้อ (เลขซ้ำ → ต่อท้าย #2, #3) — id เปลี่ยนเมื่อแก้เลขข้อ จึงจับคู่ด้วยเลขข้อ"""
    out: dict[str, dict] = {}
    for p in d.get("provisions") or []:
        k, n = p["number"], 2
        while k in out:
            k, n = f"{p['number']} #{n}", n + 1
        out[k] = {"number": p["number"], "part": p.get("part") or "", "text": p["versions"][0]["text"]}
    return out


_CLUSTER = re.compile(r"[เแโใไ]?[\u0e01-\u0e2e\u0e4f-\u0e5b][\u0e30-\u0e3a\u0e45\u0e47-\u0e4e]*|\s+|[^\u0e00-\u0e7f\s]+|.", re.S)


def _words(t: str) -> list[str]:
    """แบ่งคำไทยให้ผลเทียบอ่านได้ (ไม่ตัดกลางสระ/วรรณยุกต์); ถ้าตัดคำไม่ได้ใช้กลุ่มอักษร"""
    try:
        from pythainlp.tokenize import word_tokenize
        w = word_tokenize(t, engine="newmm", keep_whitespace=True)
        if "".join(w) == t:
            return w
    except Exception:  # ไม่มี pythainlp หรือตัดคำพลาด → ใช้กลุ่มอักษรแทน
        log.debug("word_tokenize failed; falling back to clusters", exc_info=True)
    return _CLUSTER.findall(t)


def _segments(a: str, b: str) -> list[dict]:
    """ส่วนที่เหมือน/ลบ/เพิ่ม — เทียบทีละบรรทัดก่อน แล้วเทียบทีละคำในช่วงที่ต่างกัน"""
    out: list[dict] = []

    def push(op: str, t: str) -> None:
        if not t:
            return
        if out and out[-1]["op"] == op:
            out[-1]["text"] += t
        else:
            out.append({"op": op, "text": t})

    la, lb = a.splitlines(keepends=True), b.splitlines(keepends=True)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        x, y = "".join(la[i1:i2]), "".join(lb[j1:j2])
        if tag == "equal":
            push("eq", x)
        elif tag == "replace" and len(x) <= 8000 and len(y) <= 8000:
            tx, ty = _words(x), _words(y)
            for t2, a1, a2, b1, b2 in difflib.SequenceMatcher(None, tx, ty, autojunk=False).get_opcodes():
                if t2 == "equal":
                    push("eq", "".join(tx[a1:a2]))
                else:
                    push("del", "".join(tx[a1:a2]))
                    push("ins", "".join(ty[b1:b2]))
        else:
            push("del", x)
            push("ins", y)
    return out


def diff_drafts(old: dict, new: dict) -> dict:
    fields = [{"field": k, "old": old.get(k), "new": new.get(k)} for k in INST_FIELDS if old.get(k) != new.get(k)]
    ko, kn = _keyed(old), _keyed(new)
    provs = []
    for k, p in kn.items():
        o = ko.get(k)
        if o is None:
            provs.append({"key": k, "change": "added", "new": p, "segments": [{"op": "ins", "text": p["text"]}]})
        elif o != p:
            provs.append({"key": k, "change": "changed", "old": o, "new": p,
                          "part_changed": o["part"] != p["part"], "segments": _segments(o["text"], p["text"])})
    provs += [{"key": k, "change": "removed", "old": o, "segments": [{"op": "del", "text": o["text"]}]}
              for k, o in ko.items() if k not in kn]
    return {"fields": fields, "provisions": provs}


def summarize(diff: dict) -> dict:
    by = lambda c: [p["key"] for p in diff["provisions"] if p["change"] == c][:50]  # noqa: E731
    return {"fields": [f["field"] for f in diff["fields"]], "added": by("added"), "removed": by("removed"),
            "changed": by("changed")}


# ---------------------------------------------------------------- extract + parse
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
    raw = "" if force_ocr else docparse.pdf_text(path)
    ps = docparse.layout_paragraphs(raw) if raw.strip() else []
    thai = len(docparse.THAI_RE.findall("\n".join(ps)))
    if force_ocr or thai < 300 or docparse.tone_ratio("\n".join(ps)) < docparse.MIN_TONE_RATIO:
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
        inst, why, _ = docparse.build_instrument(paras, effective_date=clock.today().isoformat(), **args)
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
    from ..config import public_data_only
    if public_data_only() and inst.get("classification") == "internal":
        raise ValueError("เอกสารนี้เข้าหมวดบริหารงานภายใน — เครื่องนี้เก็บเฉพาะข้อมูลสาธารณะ (PUBLIC_DATA_ONLY) "
                         "นำเข้าบนเครื่องของสำนักงานแทน แล้วลบรายการนี้")
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
            again = c.execute(text("SELECT 1 FROM law_upload_revision WHERE upload_id = :i LIMIT 1"), {"i": upload_id}).scalar()
            add_revision(c, upload_id, "reprocess" if again else "extract", res["draft"], "system",
                         {"method": res["method"]})
    except Exception as e:
        log.warning("law upload %s failed: %s", upload_id, e)
        with eng.begin() as c:
            c.execute(text("UPDATE law_upload SET status = 'failed', error = :e WHERE upload_id = :i"),
                      {"e": str(e)[:500], "i": upload_id})


def process_in_background(upload_id: int) -> None:
    threading.Thread(target=process, args=(upload_id,), daemon=True, name=f"law-upload-{upload_id}").start()


# ---------------------------------------------------------------- publish
def _publish(conn: Connection, inst: dict, actor: str, src: str, kind: str = "web_upload") -> None:
    from ..seed.seed import load_laws
    load_laws(conn, [inst])
    conn.execute(text("""
        INSERT INTO law_import_log (kind, source_name, source_sha256, instrument_eli, title_th, provisions, versions,
                                    actor, detail)
        VALUES (:kind, :src, :sha, :eli, :title, :np, :nv, :actor, CAST(:detail AS jsonb))"""), {
        "kind": kind, "src": src, "sha": inst["sourceFiles"][0]["sha256"], "eli": inst["id"], "title": inst["title"],
        "np": len(inst["provisions"]), "nv": sum(len(p["versions"]) for p in inst["provisions"]), "actor": actor,
        "detail": json.dumps({"origin": inst.get("source"), "source_files": inst["sourceFiles"]}, ensure_ascii=False)})
    for note in inst.get("importNotes", []):
        conn.execute(text("INSERT INTO law_open_issue (instrument_eli, kind, note) VALUES (:i, 'import_note', :n)"),
                     {"i": inst["id"], "n": note})
    for f in inst["sourceFiles"]:
        conn.execute(text("""INSERT INTO raw_file_link (sha256, instrument_eli, role) VALUES (:s, :e, :r)
                             ON CONFLICT DO NOTHING"""), {"s": f["sha256"], "e": inst["id"], "r": f["role"]})


def _unpublish(conn: Connection, eli: str) -> None:
    """ลบตัวบทที่เผยแพร่ (ก่อนแทนที่ด้วยฉบับแก้ไข) — ทุกตารางที่อ้างถึงมี FK เป็นลูกโซ่ instrument → provision → version"""
    conn.execute(text("""DELETE FROM search_chunk WHERE kind = 'provision' AND ref_id IN (
                           SELECT pv.version_id FROM provision_version pv JOIN provision p USING (provision_id)
                           JOIN legal_instrument i USING (instrument_id) WHERE i.eli_id = :e)"""), {"e": eli})
    conn.execute(text("""DELETE FROM legal_relation WHERE version_id IN (
                           SELECT pv.version_id FROM provision_version pv JOIN provision p USING (provision_id)
                           JOIN legal_instrument i USING (instrument_id) WHERE i.eli_id = :e)"""), {"e": eli})
    conn.execute(text("""DELETE FROM provision_version WHERE provision_id IN (
                           SELECT p.provision_id FROM provision p JOIN legal_instrument i USING (instrument_id)
                           WHERE i.eli_id = :e)"""), {"e": eli})
    conn.execute(text("""DELETE FROM provision WHERE instrument_id IN (
                           SELECT instrument_id FROM legal_instrument WHERE eli_id = :e)"""), {"e": eli})
    conn.execute(text("DELETE FROM legal_instrument WHERE eli_id = :e"), {"e": eli})
    conn.execute(text("DELETE FROM law_open_issue WHERE instrument_eli = :e AND kind = 'import_note'"), {"e": eli})


def load_approved(conn: Connection) -> int:
    """seed: นำเข้าฉบับที่อนุมัติจากหน้าเว็บซ้ำ (ข้าม ELI ที่ไฟล์ seed มีแล้ว)"""
    try:
        with conn.begin_nested():
            ups = rows(conn, "SELECT * FROM law_upload WHERE status = 'approved' ORDER BY upload_id")
    except Exception:
        return 0
    have = {r[0] for r in conn.execute(text("SELECT eli_id FROM legal_instrument"))}
    from ..config import public_data_only
    n = 0
    for u in ups:
        if u["draft"]["id"] in have or (public_data_only() and u["draft"].get("classification") == "internal"):
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
    us = rows(conn, "SELECT * FROM law_upload ORDER BY upload_id DESC LIMIT 200")
    eds = _editors(conn, [u["upload_id"] for u in us])
    items = [_view(u, me=user, editors=eds.get(u["upload_id"], [])) for u in us]
    return {"items": items, "doc_types": list(DOC_TYPES), "me": user.user_id}


@router.get("/register-options")
def register_options(q: str = Query(default="", max_length=Q_MAX), user: User = Depends(current_user),
                     conn: Connection = Depends(get_conn)):
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
    from ..config import public_data_only
    if public_data_only() and classification == "internal":
        raise HTTPException(400, "เครื่องนี้ตั้งค่าเก็บเฉพาะข้อมูลสาธารณะ (PUBLIC_DATA_ONLY) — นำเข้าเอกสารภายในบนเครื่องของสำนักงาน")
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
    from .. import filecheck
    try:
        fc = filecheck.check(data, ext)
    except filecheck.FileRejected as e:
        with get_engine().begin() as c3:   # บันทึกการปฏิเสธแม้คำขอนี้ไม่สำเร็จ
            audit.record(c3, user, "law.upload.reject_file", "law_upload", outcome="DENY",
                         detail={"name": name, "reason": str(e), "signature": e.signature, "bytes": len(data)})
        raise HTTPException(e.status, str(e)) from e
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
                 detail={"name": name, "sha256": st["sha256"], "classification": classification, "register_id": register_id,
                         "av": fc.av, "pages": fc.pages})
    process_in_background(uid)
    return _view(_get(conn, uid))


@router.get("/{upload_id}")
def get_upload(upload_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    return _full(conn, upload_id, user)


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
        from ..config import public_data_only
        if body.classification == "internal" and public_data_only():
            raise HTTPException(400, "เครื่องนี้ตั้งค่าเก็บเฉพาะข้อมูลสาธารณะ (PUBLIC_DATA_ONLY)")
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
    summary = summarize(diff_drafts(u["draft"], d))
    if not any(summary.values()):
        return _full(conn, upload_id, user)   # ไม่มีอะไรเปลี่ยน — ไม่สร้างรุ่นใหม่
    conn.execute(text("""UPDATE law_upload SET draft = CAST(:d AS jsonb), edited_by = :u, edited_at = now()
                         WHERE upload_id = :i"""), {"d": json.dumps(d, ensure_ascii=False), "u": user.user_id, "i": upload_id})
    rev = add_revision(conn, upload_id, "edit", d, user.user_id, summary)
    audit.record(conn, user, "law.upload.edit", f"law_upload/{upload_id}", detail={"rev": rev, **summary})
    return _full(conn, upload_id, user)


class Review(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/{upload_id}/approve")
def approve(upload_id: int, body: Review, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] != "submitted":
        raise HTTPException(409, "อนุมัติได้เฉพาะรายการที่ส่งตรวจแล้ว")
    who = participants(u, _editors(conn, [upload_id]).get(upload_id, []))
    if user.user_id in who:
        raise HTTPException(403, "ผู้อนุมัติต้องไม่ใช่ผู้นำเข้า ผู้แก้ร่าง หรือผู้ส่งตรวจ (หลักสี่ตา)")
    inst = u["draft"]
    if not inst.get("effectiveDate"):
        raise HTTPException(400, "ระบุวันใช้บังคับก่อนอนุมัติ")
    from ..search.indexer import reindex_laws, reindex_register
    from . import relations
    if u["amends_eli"]:
        if inst["id"] != u["amends_eli"]:
            raise HTTPException(409, "รหัสตราสารของคำขอแก้ไขไม่ตรงกับฉบับเดิม")
        _unpublish(conn, inst["id"])
        _publish(conn, inst, user.user_id, f"web/upload-{upload_id}", kind="web_correction")
        conn.execute(text("UPDATE law_upload SET status = 'superseded' WHERE upload_id = :o AND status = 'approved'"),
                     {"o": u["amends_upload"]})
    elif conn.execute(text("SELECT 1 FROM legal_instrument WHERE eli_id = :e"), {"e": inst["id"]}).scalar():
        raise HTTPException(409, f"มีตราสาร {inst['id']} ในระบบแล้ว")
    else:
        _publish(conn, inst, user.user_id, f"web/upload-{upload_id}")
    if u["register_id"] is not None and not u["amends_eli"]:
        conn.execute(text("""UPDATE law_register SET instrument_eli = :e
                             WHERE register_id = :r AND instrument_eli IS NULL"""), {"e": inst["id"], "r": u["register_id"]})
        reindex_register(conn)
    relations.rebuild(conn)
    reindex_laws(conn, only_eli=inst["id"])
    conn.execute(text("""UPDATE law_upload SET status = 'approved', reviewed_by = :u, reviewed_at = now(), review_note = :n
                         WHERE upload_id = :i"""), {"u": user.user_id, "n": body.note, "i": upload_id})
    audit.record(conn, user, "law.upload.approve", f"law_upload/{upload_id}",
                 detail={"eli": inst["id"], "provisions": len(inst["provisions"]), "uploaded_by": u["uploaded_by"],
                         "participants": sorted(who), "correction_of": u["amends_upload"]})
    _after_publish_embedding()
    return _full(conn, upload_id, user)


@router.post("/{upload_id}/submit")
def submit(upload_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ส่งร่างให้ผู้อนุมัติ — หลังส่งแล้วแก้ไม่ได้ จนกว่าจะถอนหรือถูกส่งกลับ"""
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] != "draft":
        raise HTTPException(409, "ส่งตรวจได้เฉพาะรายการที่เป็นร่าง")
    if not u["draft"].get("effectiveDate"):
        raise HTTPException(400, "ระบุวันใช้บังคับก่อนส่งตรวจ")
    conn.execute(text("""UPDATE law_upload SET status = 'submitted', submitted_by = :u, submitted_at = now(),
                         reviewed_by = NULL, reviewed_at = NULL, review_note = NULL WHERE upload_id = :i"""),
                 {"u": user.user_id, "i": upload_id})
    rev = conn.execute(text("SELECT max(rev_no) FROM law_upload_revision WHERE upload_id = :i"), {"i": upload_id}).scalar()
    audit.record(conn, user, "law.upload.submit", f"law_upload/{upload_id}", detail={"rev": rev})
    return _full(conn, upload_id, user)


@router.post("/{upload_id}/withdraw")
def withdraw(upload_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ผู้ส่งตรวจถอนกลับมาแก้ต่อ (ก่อนมีผู้อนุมัติ)"""
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] != "submitted":
        raise HTTPException(409, "ถอนได้เฉพาะรายการที่ส่งตรวจแล้ว")
    if user.user_id != u["submitted_by"] and user.role != "ADM":
        raise HTTPException(403, "ถอนได้เฉพาะผู้ส่งตรวจหรือผู้ดูแล")
    conn.execute(text("UPDATE law_upload SET status = 'draft' WHERE upload_id = :i"), {"i": upload_id})
    audit.record(conn, user, "law.upload.withdraw", f"law_upload/{upload_id}")
    return _full(conn, upload_id, user)


@router.post("/{upload_id}/return")
def return_for_edit(upload_id: int, body: Review, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ผู้ตรวจส่งกลับให้แก้ — กลับเป็นร่าง พร้อมหมายเหตุ"""
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] != "submitted":
        raise HTTPException(409, "ส่งกลับแก้ได้เฉพาะรายการที่ส่งตรวจแล้ว")
    if not (body.note or "").strip():
        raise HTTPException(400, "ระบุสิ่งที่ต้องแก้")
    conn.execute(text("""UPDATE law_upload SET status = 'draft', reviewed_by = :u, reviewed_at = now(), review_note = :n
                         WHERE upload_id = :i"""), {"u": user.user_id, "n": body.note.strip(), "i": upload_id})
    audit.record(conn, user, "law.upload.return", f"law_upload/{upload_id}", detail={"note": body.note.strip()})
    return _full(conn, upload_id, user)


@router.post("/{upload_id}/reject")
def reject(upload_id: int, body: Review, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    u = _get(conn, upload_id)
    if u["status"] not in ("draft", "submitted", "failed"):
        raise HTTPException(409, "ไม่รับได้เฉพาะรายการที่ยังไม่อนุมัติ")
    conn.execute(text("""UPDATE law_upload SET status = 'rejected', reviewed_by = :u, reviewed_at = now(), review_note = :n
                         WHERE upload_id = :i"""), {"u": user.user_id, "n": body.note, "i": upload_id})
    audit.record(conn, user, "law.upload.reject", f"law_upload/{upload_id}", detail={"note": body.note})
    return _full(conn, upload_id, user)


@router.post("/{upload_id}/reprocess")
def reprocess(upload_id: int, force_ocr: bool = False, user: User = Depends(current_user),
              conn: Connection = Depends(get_conn)):
    """ถอดข้อความใหม่ (เช่น บังคับ OCR) — ร่างเดิมทุกรุ่นยังอยู่ในประวัติ"""
    require_importer(user)
    u = _get(conn, upload_id)
    if u["amends_eli"]:
        raise HTTPException(409, "คำขอแก้ไขตั้งต้นจากฉบับที่เผยแพร่ — ถอดข้อความใหม่ไม่ได้")
    if u["status"] not in ("draft", "failed", "rejected"):
        raise HTTPException(409, "ถอดใหม่ได้เฉพาะร่างที่ยังไม่ส่งตรวจ")
    with get_engine().begin() as c2:
        c2.execute(text("""UPDATE law_upload SET status = 'processing', force_ocr = :o, error = NULL, draft = NULL,
                           submitted_by = NULL, submitted_at = NULL,
                           reviewed_by = NULL, reviewed_at = NULL, review_note = NULL WHERE upload_id = :i"""),
                   {"o": force_ocr or u["force_ocr"], "i": upload_id})
    audit.record(conn, user, "law.upload.reprocess", f"law_upload/{upload_id}", detail={"force_ocr": force_ocr})
    process_in_background(upload_id)
    return _view(_get(conn, upload_id))


@router.get("/{upload_id}/revisions")
def revisions(upload_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    require_importer(user)
    _get(conn, upload_id)
    items = rows(conn, """SELECT rev_no, kind, actor, summary, created_at FROM law_upload_revision
                          WHERE upload_id = :i ORDER BY rev_no""", i=upload_id)
    for r in items:
        r["created_at"] = r["created_at"].isoformat()
    return {"items": items}


@router.get("/{upload_id}/diff")
def diff(upload_id: int, base: int | None = Query(default=None, ge=1), head: int | None = Query(default=None, ge=1),
         user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """เทียบร่าง 2 รุ่น — ค่าเริ่มต้น: รุ่นตั้งต้นล่าสุด (ผลถอดข้อความหรือฉบับที่เผยแพร่) กับร่างปัจจุบัน"""
    require_importer(user)
    u = _get(conn, upload_id)

    def rev(sql: str, **kw) -> dict | None:
        r = rows(conn, f"SELECT rev_no, kind, actor, created_at, draft FROM law_upload_revision WHERE upload_id = :i {sql}",
                 i=upload_id, **kw)
        return r[0] if r else None
    b = rev("AND rev_no = :n", n=base) if base else rev(f"AND kind IN {BASE_KINDS} ORDER BY rev_no DESC LIMIT 1")
    h = rev("AND rev_no = :n", n=head) if head else rev("ORDER BY rev_no DESC LIMIT 1")
    if not b or not h:
        raise HTTPException(404, "ไม่พบรุ่นของร่างที่ต้องการเทียบ")
    meta = lambda r: {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items() if k != "draft"}  # noqa: E731
    d = diff_drafts(b["draft"], h["draft"])
    return {"base": meta(b), "head": meta(h), **d, "summary": summarize(d), "status": u["status"]}


class CorrectionRequest(BaseModel):
    eli: str = Field(max_length=200)


@router.post("/corrections")
def create_correction(body: CorrectionRequest, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """คำขอแก้ไขตัวบทที่เผยแพร่แล้ว: ตั้งร่างจากฉบับปัจจุบัน → แก้ → ส่งตรวจ → อนุมัติ → แทนที่ฉบับเดิม"""
    require_importer(user)
    orig = rows(conn, """SELECT * FROM law_upload WHERE status = 'approved' AND draft->>'id' = :e
                         ORDER BY upload_id DESC LIMIT 1""", e=body.eli)
    if not orig:
        raise HTTPException(409, "ตัวบทนี้ไม่ได้นำเข้าจากหน้าเว็บ (มาจากชุดข้อมูลตั้งต้น) — แก้ที่ไฟล์ต้นทางแล้วนำเข้าใหม่")
    o = orig[0]
    if rows(conn, """SELECT upload_id FROM law_upload WHERE amends_eli = :e AND status IN ('draft', 'submitted')""",
            e=body.eli):
        raise HTTPException(409, "มีคำขอแก้ไขตัวบทนี้ค้างอยู่แล้ว")
    draft = dict(o["draft"])
    draft["importNotes"] = [n for n in draft.get("importNotes", []) if not n.startswith("คำขอแก้ไขโดย")]
    draft["importNotes"].append(f"คำขอแก้ไขโดย {user.user_id} (แก้จากรายการที่ {o['upload_id']})")
    uid = conn.execute(text("""
        INSERT INTO law_upload (sha256, original_name, ext, classification, doc_type, register_id, register_title,
                                effective_date, status, method, draft, uploaded_by, amends_eli, amends_upload)
        VALUES (:s, :n, :e, :c, :t, :r, :rt, :d, 'draft', :m, CAST(:dr AS jsonb), :u, :eli, :o) RETURNING upload_id"""),
        {"s": o["sha256"], "n": o["original_name"], "e": o["ext"], "c": o["classification"], "t": o["doc_type"],
         "r": o["register_id"], "rt": o["register_title"], "d": o["effective_date"], "m": o["method"],
         "dr": json.dumps(draft, ensure_ascii=False), "u": user.user_id, "eli": body.eli, "o": o["upload_id"]}).scalar()
    add_revision(conn, uid, "correction", draft, user.user_id, {"from_upload": o["upload_id"]})
    audit.record(conn, user, "law.upload.correction", f"law_upload/{uid}", detail={"eli": body.eli, "from_upload": o["upload_id"]})
    return _full(conn, uid, user)


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
