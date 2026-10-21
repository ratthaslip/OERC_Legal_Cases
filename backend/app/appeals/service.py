"""การทำงานกับฐานข้อมูลของงานอุทธรณ์/ร้องเรียน — ใช้ร่วมกันระหว่าง API (router) และ seed

ทุกฟังก์ชันรับ on_date จากผู้เรียก (API ส่งวันนี้, seed ส่งวันที่ย้อนหลัง) เพื่อให้ข้อมูลสาธิตผ่านกติกาเดียวกับการใช้งานจริง
"""
import json
import re
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import one, rows
from .clocks import APPEAL_FILING_DAYS, SUBC_DAYS, SUBC_MAX_EXTENSION, legal_clocks, request_state, step_clocks
from .sla import BusinessDays
from .workflow import BY_NO, STEPS, Step, WorkflowError, complete, new_steps, revert

PREFIX = {"APPEAL": "อธ.", "COMPLAINT": "รร."}  # รูปแบบเลขรับ — ปรับตามที่ฝ่ายคดีใช้จริง
REQUIRED_DOCS = {
    "APPEAL": [("date", "วัน เดือน ปี ที่อุทธรณ์"), ("contact", "ชื่อและที่อยู่ที่ติดต่อได้"),
               ("grounds", "ข้อโต้แย้ง ข้อเท็จจริง/ข้อกฎหมาย และพยานหลักฐานที่อ้างอิง"), ("signature", "ลายมือชื่อผู้ยื่นอุทธรณ์")],
    "COMPLAINT": [("date", "วัน เดือน ปี ที่ยื่นเรื่องร้องเรียน"), ("contact", "ชื่อและที่อยู่ที่ติดต่อได้"),
                  ("grounds", "รายละเอียด ข้อเท็จจริง/ข้อกฎหมาย และพยานหลักฐาน (เช่น สัญญาซื้อขายไฟฟ้า หนังสือคู่สัญญา)")],
}
# milestone → ขั้นที่เป็นเจ้าของ (ใช้ตอนย้อนขั้น)
MS_STEP = {"ASSIGNED": 1, "SCREEN_DONE": 2, "CORRECTION_DONE": 3, "FACTS_DONE": 4, "SUBC_FIRST_MEETING": 5,
           "SUBC_EXT_NOTICE": 5, "SUBC_EXTENDED_DUE": 5, "SUBC_REPORT": 5, "ERC_AGENDA_SENT": 6, "ERC_RECEIVED": 6,
           "ERC_RESOLUTION": 7, "NOTIFY_SENT": 8, "NOTIFY_RECEIVED": 8, "CLOSED": 9}


class ServiceError(Exception):
    def __init__(self, code: str, detail: str, status: int = 422):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


# ------------------------------------------------------------------ helpers
def calendar(conn: Connection) -> BusinessDays:
    return BusinessDays({r["holiday_date"] for r in rows(conn, "SELECT holiday_date FROM holiday")})


def milestones(conn: Connection, appeal_id: int) -> dict[str, date]:
    return {r["code"]: r["at_date"] for r in rows(conn, "SELECT code, at_date FROM appeal_milestone WHERE appeal_id=:i",
                                                  i=appeal_id)}


def set_milestone(conn: Connection, appeal_id: int, code: str, at: date, actor: str | None, note: str | None = None):
    conn.execute(text("""INSERT INTO appeal_milestone (appeal_id, code, at_date, note, actor_id)
                         VALUES (:i, :c, :d, :n, :a)
                         ON CONFLICT (appeal_id, code) DO UPDATE SET at_date = EXCLUDED.at_date, note = EXCLUDED.note,
                                                                     actor_id = EXCLUDED.actor_id"""),
                 {"i": appeal_id, "c": code, "d": at, "n": note, "a": actor})


def load_steps(conn: Connection, appeal_id: int) -> list[Step]:
    return [Step(r["step_no"], r["state"]) for r in
            rows(conn, "SELECT step_no, state FROM appeal_step WHERE appeal_id=:i ORDER BY step_no", i=appeal_id)]


def save_steps(conn: Connection, appeal_id: int, new: list[Step], on_date: date, note: str | None = None) -> None:
    old = {s.step_no: s.state for s in load_steps(conn, appeal_id)}
    conn.execute(text("UPDATE appeal_step SET state='pending' WHERE appeal_id=:i AND state='current'"), {"i": appeal_id})
    for s in new:
        p = {"i": appeal_id, "n": s.step_no, "d": on_date, "note": note}
        if s.state == "completed" and old[s.step_no] != "completed":
            conn.execute(text("""UPDATE appeal_step SET state='completed', completed_at=:d, note=coalesce(:note, note)
                                 WHERE appeal_id=:i AND step_no=:n"""), p)
        elif s.state == "current":
            conn.execute(text("""UPDATE appeal_step SET state='current', completed_at=NULL,
                                 started_at=CASE WHEN :was = 'completed' THEN started_at ELSE :d END
                                 WHERE appeal_id=:i AND step_no=:n"""), {**p, "was": old[s.step_no]})
        elif s.state in ("pending", "skipped"):
            conn.execute(text("""UPDATE appeal_step SET state=:st, started_at=NULL, completed_at=NULL
                                 WHERE appeal_id=:i AND step_no=:n"""), {**p, "st": s.state})


def bump(conn: Connection, appeal_id: int, if_match: int | None, **fields) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    sql = f"UPDATE appeal SET row_version = row_version + 1{', ' + sets if sets else ''} WHERE appeal_id = :i"
    params = {"i": appeal_id, **fields}
    if if_match is not None:
        sql += " AND row_version = :v"
        params["v"] = if_match
    if conn.execute(text(sql), params).rowcount != 1:
        raise ServiceError("VERSION_CONFLICT", "ข้อมูลถูกแก้ไขโดยผู้อื่นแล้ว กรุณาโหลดใหม่", 409)


def _not_future(d: date, today: date, what: str) -> None:
    if d > today:
        raise ServiceError("VALIDATION_FAILED", f"{what} ต้องไม่เป็นวันในอนาคต", 400)


# ------------------------------------------------------------------ actions
def register(conn: Connection, actor: str, data: dict, today: date) -> int:
    """ข้อ 5.1 รับเรื่องและลงทะเบียน (ขั้น 1 เริ่ม)"""
    ct = data["case_type"]
    if ct not in PREFIX:
        raise ServiceError("VALIDATION_FAILED", "ประเภทเรื่องต้องเป็น APPEAL หรือ COMPLAINT", 400)
    received = data["received_date"]
    _not_future(received, today, "วันที่ยื่น")
    year = received.year + 543
    no = data.get("appeal_no")
    if not no:
        n = conn.execute(text("SELECT count(*) FROM appeal WHERE case_type=:t AND appeal_no LIKE :y"),
                         {"t": ct, "y": f"%/{year}"}).scalar() + 1
        no = f"{PREFIX[ct]} {n}/{year}"
        while conn.execute(text("SELECT 1 FROM appeal WHERE appeal_no_norm=:n"), {"n": re.sub(r"[\s.]", "", no)}).first():
            n += 1
            no = f"{PREFIX[ct]} {n}/{year}"
    aid = conn.execute(text("""
        INSERT INTO appeal (appeal_no, appeal_no_norm, case_type, complaint_basis, target_type, esarabun_no, filing_channel,
                            subject, summary, party_code, party_label, respondent, energy_sector, incident_date,
                            notice_date, received_date, sla_days, classification)
        VALUES (:no, :norm, :ct, :basis, :target, :esarabun, :channel, :subject, :summary, :pcode, :plabel, :resp,
                :sector, :incident, :notice, :received, :sla, :cls) RETURNING appeal_id"""), {
        "no": no, "norm": re.sub(r"[\s.]", "", no), "ct": ct, "basis": data.get("complaint_basis"),
        "target": data.get("target_type"), "esarabun": data.get("esarabun_no"), "channel": data.get("filing_channel"),
        "subject": data["subject"], "summary": data.get("summary") or data["subject"],
        "pcode": data["party_code"], "plabel": data.get("party_label") or f"คู่กรณี รหัส [{data['party_code']}]",
        "resp": data.get("respondent"), "sector": data["energy_sector"], "incident": data.get("incident_date"),
        "notice": data.get("notice_date"), "received": received,
        "sla": 90 if ct == "APPEAL" else data.get("sla_days"), "cls": data.get("classification", "internal"),
    }).scalar()
    for s in new_steps():
        conn.execute(text("INSERT INTO appeal_step (appeal_id, step_no, state, started_at) VALUES (:i, :n, :s, :d)"),
                     {"i": aid, "n": s.step_no, "s": s.state, "d": received if s.state == "current" else None})
    set_milestone(conn, aid, "RECEIVED", received, actor, "รับเรื่องเข้าระบบ E-Sarabun")
    return aid


def assign(conn: Connection, actor: str, appeal_id: int, officer: str, on_date: date, if_match: int | None,
           esarabun_no: str | None = None) -> None:
    """ข้อ 5.1(2) ผอ.คบ. มอบหมายเจ้าหน้าที่ — ปิดขั้น 1"""
    steps = load_steps(conn, appeal_id)
    tr = _complete(steps, 1)
    conn.execute(text("DELETE FROM appeal_owner WHERE appeal_id=:i"), {"i": appeal_id})
    conn.execute(text("INSERT INTO appeal_owner VALUES (:i, :u)"), {"i": appeal_id, "u": officer})
    set_milestone(conn, appeal_id, "ASSIGNED", on_date, actor, f"มอบหมาย {officer}")
    save_steps(conn, appeal_id, tr.steps, on_date)
    bump(conn, appeal_id, if_match, **({"esarabun_no": esarabun_no} if esarabun_no else {}))


def _complete(steps: list[Step], step_no: int, **kw):
    try:
        return complete(steps, step_no, **kw)
    except WorkflowError as e:
        raise ServiceError("WORKFLOW_INVALID_STEP", str(e))


def screen_result(a: dict, checks: dict) -> dict:
    """ประเมินผลตรวจขั้น 2 — ระยะเวลาคำนวณจากวันที่ ไม่ให้กรอกเอง"""
    timely = None
    if a["case_type"] == "APPEAL" and a.get("notice_date") and a.get("received_date"):
        timely = a["received_date"] <= a["notice_date"] + timedelta(days=APPEAL_FILING_DAYS)
    elif a["case_type"] == "COMPLAINT":
        timely = checks.get("timely")  # กำหนดเวลาตามระเบียบ/ประกาศ/สัญญาแต่ละฉบับ → เจ้าหน้าที่ยืนยัน
    docs = {k: bool(checks.get("docs", {}).get(k)) for k, _ in REQUIRED_DOCS[a["case_type"]]}
    return {
        "standing": bool(checks.get("standing")), "jurisdiction": bool(checks.get("jurisdiction")),
        "timely": timely, "docs": docs, "docs_complete": all(docs.values()),
        "prior_steps_done": checks.get("prior_steps_done") if a["case_type"] == "COMPLAINT" else None,
        "note": checks.get("note"),
    }


def complete_step(conn: Connection, actor: str, appeal_id: int, step_no: int, on_date: date, payload: dict,
                  if_match: int | None, today: date) -> None:
    _not_future(on_date, today, "วันที่ดำเนินการ")
    a = one(conn, "SELECT * FROM appeal WHERE appeal_id=:i", i=appeal_id)
    ms = milestones(conn, appeal_id)
    steps = load_steps(conn, appeal_id)
    fields: dict = {}
    note = payload.get("note")

    if step_no == 1:
        raise ServiceError("WORKFLOW_INVALID_STEP", "ขั้นที่ 1 ปิดด้วยการมอบหมายผู้รับผิดชอบ (ผอ.คบ.)")

    if step_no == 2:
        sc = screen_result(a, payload.get("checks", {}))
        result = payload.get("result")
        eligible = sc["standing"] and sc["jurisdiction"] and sc["timely"] is not False and \
            (sc["prior_steps_done"] is not False)
        if result == "ACCEPTED" and not (eligible and sc["docs_complete"]):
            raise ServiceError("SCREENING_INCONSISTENT", "รับเรื่องได้เมื่อผ่านทุกข้อและเอกสารครบถ้วนเท่านั้น")
        if result == "NEED_CORRECTION" and (sc["docs_complete"] or not eligible):
            raise ServiceError("SCREENING_INCONSISTENT", "แจ้งแก้ไขใช้กรณีมีสิทธิ ทันกำหนด แต่เอกสารยังไม่ครบ")
        if result == "REJECTED" and eligible:
            raise ServiceError("SCREENING_INCONSISTENT", "เสนอไม่รับได้เมื่อไม่มีสิทธิ ยื่นพ้นกำหนด หรือไม่อยู่ในอำนาจ กกพ.")
        if result == "REJECTED" and not note:
            raise ServiceError("VALIDATION_FAILED", "ต้องระบุเหตุผลประกอบความเห็นไม่รับ (หลักการรับฟังคู่กรณีและการให้เหตุผล)", 400)
        tr = _complete(steps, 2, screening_result=result, route=payload.get("route"))
        fields = {"screening": json.dumps({**sc, "result": result}, ensure_ascii=False),
                  "admissibility": {"ACCEPTED": "ACCEPTED", "REJECTED": "REJECTED"}.get(result, "PENDING")}
        if result == "ACCEPTED":
            fields["register_no"] = payload.get("register_no") or a["register_no"]
        if result == "REJECTED":
            fields.update(route=payload["route"], route_reason=payload.get("route_reason"))

    elif step_no == 3:
        open_ = rows(conn, """SELECT 1 FROM appeal_request WHERE appeal_id=:i AND kind='CORRECTION'
                              AND responded_date IS NULL""", i=appeal_id)
        if open_ and not payload.get("force_reason"):
            raise ServiceError("OPEN_REQUESTS", "ยังมีหนังสือแจ้งแก้ไขที่ยังไม่ได้รับคำตอบ (ระบุเหตุผลหากจะดำเนินการต่อ)")
        if not rows(conn, "SELECT 1 FROM appeal_request WHERE appeal_id=:i AND kind='CORRECTION'", i=appeal_id):
            raise ServiceError("VALIDATION_FAILED", "ต้องบันทึกหนังสือแจ้งให้แก้ไขอย่างน้อย 1 ฉบับ", 400)
        tr = _complete(steps, 3)
        fields = {"admissibility": "ACCEPTED", "register_no": payload.get("register_no") or a["register_no"]}

    elif step_no == 4:
        open_ = rows(conn, """SELECT 1 FROM appeal_request WHERE appeal_id=:i AND kind='INFO'
                              AND responded_date IS NULL""", i=appeal_id)
        if open_ and not payload.get("force_reason"):
            raise ServiceError("OPEN_REQUESTS", "ยังมีหนังสือขอข้อเท็จจริงที่ยังไม่ได้รับคำตอบ (ระบุเหตุผลหากจะดำเนินการต่อ)")
        route = payload.get("route")
        if route == "DIRECT" and not payload.get("route_reason"):
            raise ServiceError("VALIDATION_FAILED", "การเสนอ กกพ. โดยไม่ผ่านอนุกรรมการฯ ต้องระบุเหตุ (มีแนวคำวินิจฉัยแล้ว/เรื่องเร่งด่วน)", 400)
        tr = _complete(steps, 4, route=route)
        fields = {"route": route, "route_reason": payload.get("route_reason")}

    elif step_no == 5:
        if "SUBC_FIRST_MEETING" not in ms:
            raise ServiceError("MISSING_MILESTONE", "ต้องบันทึกวันประชุมคณะอนุกรรมการฯ ครั้งแรกก่อน")
        tr = _complete(steps, 5)

    elif step_no == 6:
        sent = payload.get("agenda_sent_date") or on_date
        set_milestone(conn, appeal_id, "ERC_AGENDA_SENT", sent, actor, "ส่งวาระเสนอ กกพ.")
        tr = _complete(steps, 6)

    elif step_no == 7:
        if not payload.get("result"):
            raise ServiceError("VALIDATION_FAILED", "ต้องระบุผลคำวินิจฉัยตามมติ กกพ.", 400)
        tr = _complete(steps, 7)
        fields = {"decision_result": payload["result"], "erc_meeting_ref": payload.get("meeting_ref")}

    elif step_no == 8:
        if a["case_type"] == "APPEAL" and not payload.get("rights_notice"):
            raise ServiceError("VALIDATION_FAILED", "หนังสือแจ้งคำวินิจฉัยอุทธรณ์ต้องแจ้งสิทธิ วิธีการ และระยะเวลาฟ้องคดีต่อศาลปกครอง", 400)
        tr = _complete(steps, 8)

    elif step_no == 9:
        if "NOTIFY_RECEIVED" not in ms:
            raise ServiceError("MISSING_MILESTONE", "ต้องบันทึกวันที่ผู้อุทธรณ์ได้รับหนังสือแจ้งผล (ใบตอบรับ) ก่อนปิดเรื่อง")
        tr = _complete(steps, 9)
        fields = {"status": "CLOSED"}
    else:
        raise ServiceError("WORKFLOW_INVALID_STEP", "ไม่มีขั้นตอนนี้")

    out = BY_NO[step_no].output
    if out:
        set_milestone(conn, appeal_id, out, on_date, actor, note)
    save_steps(conn, appeal_id, tr.steps, on_date, note)
    bump(conn, appeal_id, if_match, **fields)


def add_milestone(conn: Connection, actor: str, appeal_id: int, code: str, at: date, note: str | None,
                  if_match: int | None, today: date, extended_due: date | None = None) -> None:
    """เหตุการณ์ระหว่างขั้น: นัดประชุมอนุฯ ครั้งแรก, แจ้งขยายเวลาอนุฯ, ใบตอบรับหนังสือแจ้งผล"""
    _not_future(at, today, "วันที่")
    cur = next((s.step_no for s in load_steps(conn, appeal_id) if s.state == "current"), None)
    ms = milestones(conn, appeal_id)
    if code == "SUBC_FIRST_MEETING":
        if cur != 5:
            raise ServiceError("WORKFLOW_INVALID_STEP", "บันทึกวันประชุมอนุฯ ได้เมื่ออยู่ขั้นที่ 5")
    elif code == "SUBC_EXT_NOTICE":
        if cur != 5 or "SUBC_FIRST_MEETING" not in ms:
            raise ServiceError("WORKFLOW_INVALID_STEP", "ขยายเวลาได้เมื่ออยู่ระหว่างการพิจารณาของคณะอนุกรรมการฯ")
        if "SUBC_EXTENDED_DUE" in ms:
            raise ServiceError("EXTENSION_USED", "ขยายระยะเวลาได้ครั้งเดียว ไม่เกิน 30 วัน")
        original = ms["SUBC_FIRST_MEETING"] + timedelta(days=SUBC_DAYS)
        if at > original:
            raise ServiceError("EXTENSION_LATE", "ต้องมีหนังสือแจ้งผู้อุทธรณ์ก่อนครบกำหนด 30 วัน (ม.45 วิ.ปกครอง)")
        new_due = extended_due or original + timedelta(days=SUBC_MAX_EXTENSION)
        if new_due > original + timedelta(days=SUBC_MAX_EXTENSION) or new_due <= original:
            raise ServiceError("EXTENSION_TOO_LONG", "ขยายได้ไม่เกิน 30 วันนับแต่วันครบกำหนดเดิม")
        set_milestone(conn, appeal_id, "SUBC_EXTENDED_DUE", new_due, actor, "ขยายระยะเวลาการพิจารณาของอนุฯ")
    elif code == "NOTIFY_RECEIVED":
        if cur != 9 or "NOTIFY_SENT" not in ms:
            raise ServiceError("WORKFLOW_INVALID_STEP", "บันทึกใบตอบรับได้หลังส่งหนังสือแจ้งผลแล้ว")
        if at < ms["NOTIFY_SENT"]:
            raise ServiceError("VALIDATION_FAILED", "วันรับหนังสือต้องไม่ก่อนวันส่ง", 400)
    else:
        raise ServiceError("VALIDATION_FAILED", "บันทึกเหตุการณ์นี้ผ่านการปิดขั้นตอนแทน", 400)
    set_milestone(conn, appeal_id, code, at, actor, note)
    bump(conn, appeal_id, if_match)


def add_request(conn: Connection, actor: str, appeal_id: int, data: dict, if_match: int | None, today: date) -> None:
    """ข้อ 5.2(2) หนังสือแจ้งแก้ไข (ขั้น 3) / ข้อ 5.4 หนังสือขอข้อเท็จจริง (ขั้น 4) — ค่าเริ่มต้นให้ตอบภายใน 7 วัน"""
    cur = next((s.step_no for s in load_steps(conn, appeal_id) if s.state == "current"), None)
    kind = "CORRECTION" if cur == 3 else "INFO" if cur in (4, 5) else None
    if kind is None:
        raise ServiceError("WORKFLOW_INVALID_STEP", "ออกหนังสือได้ในขั้นแจ้งแก้ไข (3) แสวงหาข้อเท็จจริง (4) หรือระหว่างพิจารณาของอนุฯ (5)")
    issued = data.get("issued_date") or today
    _not_future(issued, today, "วันที่ออกหนังสือ")
    due = data.get("due_date") or issued + timedelta(days=7)
    conn.execute(text("""INSERT INTO appeal_request (appeal_id, step_no, kind, target_type, target_name, subject, doc_no,
                                                     issued_date, due_date, note)
                         VALUES (:i, :s, :k, :tt, :tn, :sub, :doc, :iss, :due, :note)"""),
                 {"i": appeal_id, "s": cur, "k": kind, "tt": data["target_type"], "tn": data["target_name"],
                  "sub": data["subject"], "doc": data.get("doc_no"), "iss": issued, "due": due, "note": data.get("note")})
    bump(conn, appeal_id, if_match)


def respond_request(conn: Connection, appeal_id: int, request_id: int, on_date: date, note: str | None,
                    if_match: int | None, today: date) -> None:
    _not_future(on_date, today, "วันที่ได้รับคำตอบ")
    r = conn.execute(text("""UPDATE appeal_request SET responded_date=:d, note=coalesce(:n, note)
                             WHERE request_id=:r AND appeal_id=:i AND responded_date IS NULL"""),
                     {"d": on_date, "n": note, "r": request_id, "i": appeal_id})
    if r.rowcount != 1:
        raise ServiceError("NOT_FOUND", "ไม่พบหนังสือที่รอคำตอบ", 404)
    bump(conn, appeal_id, if_match)


def revert_step(conn: Connection, actor: str, appeal_id: int, reason: str, on_date: date, if_match: int | None) -> int:
    if not reason or not reason.strip():
        raise ServiceError("VALIDATION_FAILED", "ต้องระบุเหตุผลการย้อนขั้นตอน", 400)
    try:
        new, back = revert(load_steps(conn, appeal_id))
    except WorkflowError as e:
        raise ServiceError("WORKFLOW_INVALID_STEP", str(e))
    if back == 1:
        raise ServiceError("WORKFLOW_INVALID_STEP", "ย้อนกลับไปขั้นรับเรื่องไม่ได้ ให้ ผอ.คบ. มอบหมายใหม่แทน")
    drop = [c for c, n in MS_STEP.items() if n >= back]
    conn.execute(text("DELETE FROM appeal_milestone WHERE appeal_id=:i AND code = ANY(:c)"), {"i": appeal_id, "c": drop})
    fields: dict = {"status": "IN_PROGRESS"}
    if back <= 7:
        fields["decision_result"] = None
    if back <= 4:
        fields.update(route=None, route_reason=None)
    if back <= 2:
        fields.update(admissibility="PENDING")
    save_steps(conn, appeal_id, new, on_date, f"ย้อนขั้น: {reason}")
    bump(conn, appeal_id, if_match, **fields)
    return back


# ------------------------------------------------------------------ read models
def detail(conn: Connection, appeal_id: int, today: date, near_due_days: int) -> dict:
    a = one(conn, "SELECT * FROM appeal WHERE appeal_id=:i", i=appeal_id)
    ms = milestones(conn, appeal_id)
    cal = calendar(conn)
    steps = rows(conn, """SELECT step_no, state, started_at, completed_at, note FROM appeal_step
                          WHERE appeal_id=:i ORDER BY step_no""", i=appeal_id)
    reqs = rows(conn, "SELECT * FROM appeal_request WHERE appeal_id=:i ORDER BY issued_date, request_id", i=appeal_id)
    for r in reqs:
        r["state"] = request_state(r, today)
    ms_rows = rows(conn, "SELECT code, at_date, note, actor_id FROM appeal_milestone WHERE appeal_id=:i ORDER BY at_date",
                   i=appeal_id)
    return {
        "appeal": a,
        "steps": step_clocks(steps, ms, today, cal),
        "clocks": legal_clocks(a, ms, today, near_due_days),
        "milestones": ms_rows,
        "requests": reqs,
        "required_docs": [{"key": k, "label": v} for k, v in REQUIRED_DOCS[a["case_type"]]],
    }


def summaries(conn: Connection, ids: list[int], today: date, near_due_days: int) -> dict[int, dict]:
    """ข้อมูลสรุปของหลายเรื่องสำหรับหน้ารายการ (ขั้นปัจจุบัน + สถานะกรอบเวลา)"""
    if not ids:
        return {}
    cal = calendar(conn)
    ms_all: dict[int, dict] = {i: {} for i in ids}
    for r in rows(conn, "SELECT appeal_id, code, at_date FROM appeal_milestone WHERE appeal_id = ANY(:ids)", ids=ids):
        ms_all[r["appeal_id"]][r["code"]] = r["at_date"]
    st_all: dict[int, list] = {i: [] for i in ids}
    for r in rows(conn, """SELECT appeal_id, step_no, state, started_at, completed_at, note FROM appeal_step
                           WHERE appeal_id = ANY(:ids) ORDER BY step_no""", ids=ids):
        st_all[r.pop("appeal_id")].append(r)
    open_req = {r["appeal_id"]: r for r in rows(conn, """
        SELECT appeal_id, count(*) AS n, count(*) FILTER (WHERE due_date < :t) AS overdue
        FROM appeal_request WHERE appeal_id = ANY(:ids) AND responded_date IS NULL GROUP BY 1""", ids=ids, t=today)}
    courts = {r["appeal_id"]: r["n"] for r in rows(conn, """SELECT appeal_id, count(*) AS n FROM court_case
                                                             WHERE appeal_id = ANY(:ids) GROUP BY 1""", ids=ids)}
    out = {}
    for i in ids:
        steps = step_clocks(st_all[i], ms_all[i], today, cal)
        cur = next((s for s in steps if s["state"] == "current"), None)
        out[i] = {
            "current_step": None if cur is None else {k: cur.get(k) for k in
                                                      ("step_no", "name", "due_date", "timing", "days_left")},
            "open_requests": int(open_req.get(i, {}).get("n", 0)),
            "overdue_requests": int(open_req.get(i, {}).get("overdue", 0)),
            "court_case_count": courts.get(i, 0),
            "_ms": ms_all[i],
        }
    return out
