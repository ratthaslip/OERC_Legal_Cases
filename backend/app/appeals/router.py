"""API งานอุทธรณ์และเรื่องร้องเรียน (ตามคู่มือปฏิบัติงาน WI-EFL-04)"""
from datetime import date

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import DEMO_USERS, User, current_user
from ..config import get_settings
from ..db import get_conn, one, rows
from ..law.lookup import provision_labels
from ..search.hybrid import hybrid_search
from ..search.indexer import reindex_cases
from . import service as svc
from .workflow import STEPS

router = APIRouter(prefix="/api/v1/appeals", tags=["appeals"])

ACCESS_SQL = """(:dir OR a.classification = 'internal'
                 OR EXISTS (SELECT 1 FROM appeal_owner o WHERE o.appeal_id = a.appeal_id AND o.user_id = :uid))"""
URGENCY = {"OVERDUE": 0, "NEAR_DUE": 1, "WAITING": 2, "ON_TRACK": 3, None: 4}


def _today() -> date:
    return date.today()


def _version(if_match: str | None) -> int:
    if if_match is None:
        raise HTTPException(428, detail={"code": "PRECONDITION_REQUIRED", "detail": "ต้องส่ง If-Match (row_version)"})
    try:
        return int(if_match.strip('"'))
    except ValueError:
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "If-Match ไม่ถูกต้อง"})


def _run(fn):
    try:
        return fn()
    except svc.ServiceError as e:
        raise HTTPException(e.status, detail={"code": e.code, "detail": e.detail})


@router.get("/workflow")
def workflow_definition():
    """ขั้นตอนและกรอบเวลาตาม WI-EFL-04 (หน้าจอใช้แสดงคำอธิบาย)"""
    return {"steps": [s.__dict__ for s in STEPS], "officers": [
        {"user_id": u.user_id, "name": u.name} for u in DEMO_USERS.values() if u.role == "LGL"]}


@router.get("")
def list_appeals(q: str | None = None, sector: str | None = None, case_type: str | None = None,
                 status: str | None = Query(default=None, pattern="^(IN_PROGRESS|CLOSED)$"),
                 step: int | None = None, attention: str | None = None, mine: bool = False,
                 user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    base = rows(conn, f"""
        SELECT a.appeal_id, a.appeal_no, a.case_type, a.complaint_basis, a.subject, a.energy_sector, a.incident_date,
               a.received_date, a.sla_days, a.status, a.decision_result, a.classification, a.admissibility, a.route,
               ARRAY(SELECT user_id FROM appeal_owner o WHERE o.appeal_id = a.appeal_id) AS owners
        FROM appeal a WHERE {ACCESS_SQL}
          AND (CAST(:sector AS text) IS NULL OR a.energy_sector = :sector)
          AND (CAST(:ct AS text) IS NULL OR a.case_type = :ct)
        ORDER BY a.received_date DESC NULLS LAST""", uid=user.user_id, dir=user.role == "DIR", sector=sector,
                ct=case_type)
    omitted = 0
    if q and q.strip():
        res = hybrid_search(conn, user, q, kinds=["appeal"], limit=200, sector=sector)
        exact = [h for h in res.hits if h.exact_ref]
        order = ({h.ref_id: i for i, h in enumerate(exact)} if exact else
                 {h.ref_id: i for i, h in enumerate(res.hits) if h.lexical or h.cosine >= 0.3})
        base = sorted([a for a in base if a["appeal_id"] in order], key=lambda a: order[a["appeal_id"]])
        omitted = res.restricted_omitted

    today, near = _today(), get_settings().sla_near_due_days
    summ = svc.summaries(conn, [a["appeal_id"] for a in base], today, near)
    from .clocks import legal_clocks
    for a in base:
        s = summ[a["appeal_id"]]
        ms = s.pop("_ms")
        a.update(s)
        total = next((c for c in legal_clocks(a, ms, today, near) if c["key"] == "total"), None)
        a["total_clock"] = total
        a["owner_names"] = [DEMO_USERS[u].name if u in DEMO_USERS else u for u in a["owners"]]
        a["attention"] = (
            "OVERDUE" if (total and total["state"] == "OVERDUE") or (a["current_step"] or {}).get("timing") == "OVERDUE"
            or a["overdue_requests"] else
            "NEAR_DUE" if (total and total["state"] == "NEAR_DUE") or (a["current_step"] or {}).get("timing") == "NEAR_DUE"
            else None)

    if not (q and q.strip()):
        base.sort(key=lambda a: (a["status"] == "CLOSED", URGENCY.get(a["attention"], 4),
                                 (a["current_step"] or {}).get("days_left") or 0))
    in_prog = [a for a in base if a["status"] == "IN_PROGRESS"]
    stats = {
        "total": len(base),
        "in_progress": len(in_prog),
        "closed": len(base) - len(in_prog),
        "overdue": sum(a["attention"] == "OVERDUE" for a in in_prog),
        "near_due": sum(a["attention"] == "NEAR_DUE" for a in in_prog),
        "unassigned": sum((a["current_step"] or {}).get("step_no") == 1 for a in in_prog),
        "open_requests": sum(a["open_requests"] for a in in_prog),
        "complaints": sum(a["case_type"] == "COMPLAINT" for a in base),
        "with_court_case": sum(a["court_case_count"] > 0 for a in base),
        "by_step": {s.step_no: sum((a["current_step"] or {}).get("step_no") == s.step_no for a in in_prog) for s in STEPS},
    }
    if status:
        base = [a for a in base if a["status"] == status]
    if step:
        base = [a for a in base if (a["current_step"] or {}).get("step_no") == step]
    if attention:
        base = [a for a in base if a["attention"] == attention]
    if mine:
        base = [a for a in base if user.user_id in a["owners"]]
    sectors = [r["energy_sector"] for r in rows(conn, "SELECT DISTINCT energy_sector FROM appeal ORDER BY 1")]
    return {"items": base, "stats": stats, "restricted_omitted": omitted, "sectors": sectors}


def _load(conn: Connection, user: User, appeal_id: int) -> tuple[dict, list[str]]:
    a = one(conn, "SELECT appeal_id, classification FROM appeal WHERE appeal_id = :i", i=appeal_id)
    if not a:
        raise authz.not_found()
    owners = [r["user_id"] for r in rows(conn, "SELECT user_id FROM appeal_owner WHERE appeal_id = :i", i=appeal_id)]
    if not authz.can_read_case(user, a["classification"], owners):
        audit.record(conn, user, "appeal.view", f"appeal/{appeal_id}", "DENY")
        raise authz.not_found()
    return a, owners


def _detail(conn: Connection, user: User, appeal_id: int, owners: list[str]) -> dict:
    d = svc.detail(conn, appeal_id, _today(), get_settings().sla_near_due_days)
    i = appeal_id
    d["documents"] = rows(conn, """SELECT document_id, doc_no, doc_type, doc_date, pages, excerpt FROM case_document
                                   WHERE owner_type = 'appeal' AND owner_id = :i ORDER BY doc_date""", i=i)
    cits = rows(conn, """SELECT cited_eli_id, cited_version_no, note, state FROM citation_link
                         WHERE owner_type = 'appeal' AND owner_id = :i""", i=i)
    labels = provision_labels(conn, [c["cited_eli_id"] for c in cits])
    for c in cits:
        c["label"] = labels.get(c["cited_eli_id"], {}).get("label", c["cited_eli_id"])
    d["citations"] = cits
    court = []
    for k in rows(conn, """SELECT k.case_id, k.black_no, k.red_no, k.court_level, k.classification,
                                  ARRAY(SELECT user_id FROM court_case_owner o WHERE o.case_id = k.case_id) AS owners
                           FROM court_case k WHERE k.appeal_id = :i""", i=i):
        if authz.can_read_case(user, k["classification"], k.pop("owners")):
            court.append(k)
    d["court_cases"] = court
    d["appeal"]["owners"] = owners
    d["appeal"]["owner_names"] = [DEMO_USERS[u].name if u in DEMO_USERS else u for u in owners]
    d["can_edit"] = authz.can_edit_workflow(user, owners) and d["appeal"]["status"] != "CLOSED"
    d["can_assign"] = authz.can_assign(user)
    d["officers"] = [{"user_id": u.user_id, "name": u.name} for u in DEMO_USERS.values() if u.role == "LGL"]
    return d


@router.get("/{appeal_id}")
def get_appeal(appeal_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    a, owners = _load(conn, user, appeal_id)
    audit.record(conn, user, "appeal.view", f"appeal/{appeal_id}", detail={"classification": a["classification"]})
    return _detail(conn, user, appeal_id, owners)


def _after(conn: Connection, user: User, appeal_id: int, action: str, detail: dict) -> dict:
    audit.record(conn, user, action, f"appeal/{appeal_id}", detail=detail)
    reindex_cases(conn, only=("appeal", appeal_id))  # POC: ระบบจริงใช้ event appeal.updated ผ่าน outbox
    _, owners = _load(conn, user, appeal_id)
    return _detail(conn, user, appeal_id, owners)


def _require_edit(conn: Connection, user: User, appeal_id: int) -> None:
    authz.require_internal(user)
    _, owners = _load(conn, user, appeal_id)
    if not authz.can_edit_workflow(user, owners):
        raise authz.forbidden("เฉพาะเจ้าหน้าที่ฝ่ายคดีผู้รับผิดชอบเรื่อง หรือ ผอ.คบ. เท่านั้น")


class RegisterIn(BaseModel):
    case_type: str = Field(pattern="^(APPEAL|COMPLAINT)$")
    complaint_basis: str | None = Field(default=None, pattern="^(PROCUREMENT|PPA)$")
    target_type: str | None = Field(default=None, pattern="^(ORDER|RULE)$")
    filing_channel: str = Field(pattern="^(COUNTER|POST|ELECTRONIC)$")
    esarabun_no: str | None = None
    received_date: date
    notice_date: date | None = None
    incident_date: date | None = None
    subject: str = Field(min_length=5)
    summary: str | None = None
    party_code: str = Field(min_length=2, max_length=20)
    party_label: str | None = None
    respondent: str | None = None
    energy_sector: str
    sla_days: int | None = Field(default=None, ge=1, le=365)
    classification: str = Field(default="internal", pattern="^(internal|restricted)$")


@router.post("", status_code=201)
def register(body: RegisterIn, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    if not authz.can_register(user):
        raise authz.forbidden("ไม่มีสิทธิ์ลงทะเบียนเรื่อง")
    if body.case_type == "APPEAL" and not body.target_type:
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "อุทธรณ์ต้องระบุว่าอุทธรณ์คำสั่งหรือกฎ"})
    aid = _run(lambda: svc.register(conn, user.user_id, body.model_dump(), _today()))
    audit.record(conn, user, "appeal.register", f"appeal/{aid}", detail={"case_type": body.case_type})
    reindex_cases(conn, only=("appeal", aid))
    return {"appeal_id": aid}


@router.post("/{appeal_id}:assign")
def assign(appeal_id: int, body: dict = Body(...), if_match: str | None = Header(default=None),
           user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    _load(conn, user, appeal_id)
    if not authz.can_assign(user):
        raise authz.forbidden("เฉพาะ ผอ.คบ. เท่านั้นที่มอบหมายผู้รับผิดชอบ")
    officer = body.get("officer")
    if officer not in DEMO_USERS or DEMO_USERS[officer].role != "LGL":
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "ต้องเลือกเจ้าหน้าที่ฝ่ายคดี"})
    _run(lambda: svc.assign(conn, user.user_id, appeal_id, officer, _today(), _version(if_match),
                            body.get("esarabun_no")))
    return _after(conn, user, appeal_id, "appeal.assign", {"officer": officer})


@router.post("/{appeal_id}/steps/{step_no}:complete")
def complete_step(appeal_id: int, step_no: int, body: dict = Body(default={}),
                  if_match: str | None = Header(default=None), user: User = Depends(current_user),
                  conn: Connection = Depends(get_conn)):
    _require_edit(conn, user, appeal_id)
    on = date.fromisoformat(body["date"]) if body.get("date") else _today()
    _run(lambda: svc.complete_step(conn, user.user_id, appeal_id, step_no, on, body, _version(if_match), _today()))
    return _after(conn, user, appeal_id, f"appeal.step{step_no}.complete",
                  {k: body.get(k) for k in ("result", "route", "route_reason", "note", "force_reason")})


@router.post("/{appeal_id}/steps:revert")
def revert_step(appeal_id: int, body: dict = Body(default={}), if_match: str | None = Header(default=None),
                user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _require_edit(conn, user, appeal_id)
    back = _run(lambda: svc.revert_step(conn, user.user_id, appeal_id, body.get("reason"), _today(),
                                        _version(if_match)))
    return _after(conn, user, appeal_id, "appeal.step_revert", {"to_step": back, "reason": body.get("reason")})


@router.post("/{appeal_id}/milestones")
def add_milestone(appeal_id: int, body: dict = Body(...), if_match: str | None = Header(default=None),
                  user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _require_edit(conn, user, appeal_id)
    at = date.fromisoformat(body["date"])
    ext = date.fromisoformat(body["extended_due"]) if body.get("extended_due") else None
    _run(lambda: svc.add_milestone(conn, user.user_id, appeal_id, body.get("code"), at, body.get("note"),
                                   _version(if_match), _today(), ext))
    return _after(conn, user, appeal_id, "appeal.milestone", {"code": body.get("code"), "date": body["date"]})


@router.post("/{appeal_id}/requests")
def add_request(appeal_id: int, body: dict = Body(...), if_match: str | None = Header(default=None),
                user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    _require_edit(conn, user, appeal_id)
    for k in ("issued_date", "due_date"):
        if body.get(k):
            body[k] = date.fromisoformat(body[k])
    if body.get("target_type") not in ("APPELLANT", "INTERNAL", "EXTERNAL") or not body.get("subject") \
            or not body.get("target_name"):
        raise HTTPException(400, detail={"code": "VALIDATION_FAILED", "detail": "ต้องระบุผู้รับ ประเภทผู้รับ และประเด็นที่ขอ"})
    _run(lambda: svc.add_request(conn, user.user_id, appeal_id, body, _version(if_match), _today()))
    return _after(conn, user, appeal_id, "appeal.request", {"target_type": body["target_type"]})


@router.post("/{appeal_id}/requests/{request_id}:respond")
def respond_request(appeal_id: int, request_id: int, body: dict = Body(default={}),
                    if_match: str | None = Header(default=None), user: User = Depends(current_user),
                    conn: Connection = Depends(get_conn)):
    _require_edit(conn, user, appeal_id)
    on = date.fromisoformat(body["date"]) if body.get("date") else _today()
    _run(lambda: svc.respond_request(conn, appeal_id, request_id, on, body.get("note"), _version(if_match), _today()))
    return _after(conn, user, appeal_id, "appeal.request_responded", {"request_id": request_id})
