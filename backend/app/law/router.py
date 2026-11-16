import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..db import get_conn, one, rows
from ..search.hybrid import hybrid_search
from .resolver import NotYetInForce, Repealed, resolve

STATUS_TH = {"IN_FORCE": "ใช้บังคับ", "REPEALED": "ยกเลิก", "UNKNOWN": "ไม่ระบุ", "GROUP": "หัวกลุ่ม"}

router = APIRouter(prefix="/api/v1/laws", tags=["laws"])


@router.get("/instruments")
def list_instruments(category: str | None = None, conn: Connection = Depends(get_conn)):
    """สารบัญตราสารและรายมาตรา พร้อมสรุปของรุ่นปัจจุบัน"""
    insts = rows(conn, """SELECT instrument_id, eli_id, instrument_type, title_th, short_title, issuer, category,
                                 promulgated_date, effective_date, is_demo
                          FROM legal_instrument
                          WHERE (CAST(:cat AS text) IS NULL OR category = :cat)
                          ORDER BY promulgated_date""", cat=category)
    provs = rows(conn, """
        SELECT p.instrument_id, p.eli_id, p.provision_no, p.part_heading,
               count(pv.*) FILTER (WHERE pv.status = 'approved') AS version_count,
               (SELECT summary FROM provision_version x
                 WHERE x.provision_id = p.provision_id AND x.status = 'approved'
                 ORDER BY x.valid_from DESC LIMIT 1) AS latest_summary
        FROM provision p LEFT JOIN provision_version pv USING (provision_id)
        GROUP BY p.provision_id ORDER BY p.instrument_id, p.sort_order""")
    by_inst: dict[int, list] = {}
    for p in provs:
        by_inst.setdefault(p.pop("instrument_id"), []).append(p)
    for i in insts:
        i["provisions"] = by_inst.get(i["instrument_id"], [])
    categories = [r["category"] for r in rows(conn, "SELECT DISTINCT category FROM legal_instrument ORDER BY 1")]
    return {"items": insts, "categories": categories}


@router.get("/search")
def search_laws(q: str, as_of: date | None = None, user: User = Depends(current_user),
                conn: Connection = Depends(get_conn)):
    """ค้นตัวบท (hybrid) เฉพาะรุ่นที่มีผล ณ วันที่ที่เลือก"""
    res = hybrid_search(conn, user, q, kinds=["provision"], as_of=as_of or date.today(), limit=20)
    return {"items": res.hits}


@router.get("/provisions/{eli_id:path}")
def get_provision(eli_id: str, as_of: date | None = Query(default=None), user: User = Depends(current_user),
                  conn: Connection = Depends(get_conn)):
    as_of = as_of or date.today()
    prov = one(conn, """SELECT p.provision_id, p.eli_id, p.provision_no, p.part_heading,
                               i.eli_id AS instrument_eli, i.title_th AS instrument_title, i.instrument_type,
                               i.short_title, i.is_demo
                        FROM provision p JOIN legal_instrument i USING (instrument_id) WHERE p.eli_id = :eli""",
               eli=eli_id)
    if not prov:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "detail": "ไม่พบมาตรานี้"})

    versions = rows(conn, """SELECT version_id, version_no, body_text, valid_from, valid_to, status, amended_by, summary
                             FROM provision_version WHERE provision_id = :pid ORDER BY version_no""",
                    pid=prov["provision_id"])
    versions = [v for v in versions if authz.can_read_law_version(user, v["status"])]
    for v in versions:
        v["is_current"] = v["valid_to"] is None
        v["relations"] = rows(conn, """SELECT relation_type, target_text, target_eli_id FROM legal_relation
                                       WHERE version_id = :vid""", vid=v["version_id"])

    resolved, problem = None, None
    try:
        resolved = resolve(conn, eli_id, as_of)["version_no"]
    except NotYetInForce:
        problem = "NOT_YET_IN_FORCE"
    except Repealed:
        problem = "REPEALED"

    # อุทธรณ์/คดีที่อ้างมาตรานี้ แยกตามรุ่น — กรองสิทธิ์ที่ server และบอกจำนวนที่ถูกซ่อน
    cites = rows(conn, """
        SELECT c.cited_version_no, c.owner_type, c.owner_id, c.note,
               a.appeal_no AS ref_no, a.subject, a.incident_date, a.decision_result AS result,
               a.classification, ARRAY(SELECT user_id FROM appeal_owner o WHERE o.appeal_id = a.appeal_id) AS owners
        FROM citation_link c JOIN appeal a ON c.owner_type = 'appeal' AND a.appeal_id = c.owner_id
        WHERE c.cited_eli_id = :eli AND c.state = 'verified'
        UNION ALL
        SELECT c.cited_version_no, c.owner_type, c.owner_id, c.note,
               coalesce(k.red_no, k.black_no), k.subject, k.incident_date, k.result,
               k.classification, ARRAY(SELECT user_id FROM court_case_owner o WHERE o.case_id = k.case_id)
        FROM citation_link c JOIN court_case k ON c.owner_type = 'court_case' AND k.case_id = c.owner_id
        WHERE c.cited_eli_id = :eli AND c.state = 'verified'
        ORDER BY incident_date DESC""", eli=eli_id)
    visible, hidden = [], 0
    if user.role in authz.INTERNAL_ROLES:
        for c in cites:
            if authz.can_read_case(user, c["classification"], c["owners"]):
                c.pop("owners")
                visible.append(c)
            else:
                hidden += 1
    else:
        hidden = len(cites)  # ผู้ใช้ภายนอกไม่เห็นงานอุทธรณ์/คดี

    audit.record(conn, user, "law.view", eli_id, detail={"as_of": as_of.isoformat(), "version": resolved})
    return {
        "provision": prov, "as_of": as_of, "resolved_version_no": resolved, "problem": problem,
        "versions": versions, "citations": visible, "citations_hidden": hidden,
    }


# ---------- ทะเบียนคุมกฎหมายลำดับรอง ----------
_PUBLIC_COLS = ("register_id, reg_no, group_no, doc_type, title_th, authority_text, authority_secs, effective_text, "
                "effective_date, status, instrument_eli")
_INTERNAL_COLS = ", owner_unit, review_text, status_text, note, hearing_text, attachments, issues, sheet_row, source_file, imported_at"


@router.get("/register")
def list_register(q: str | None = None, doc_type: str | None = None, status: str | None = None,
                  section: int | None = None, unit: str | None = None, issues_only: bool = False,
                  limit: int = Query(default=50, le=400), offset: int = 0,
                  user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ทะเบียนกฎหมายลำดับรอง: กรองตามประเภท/สถานะ/มาตราแม่บท/ฝ่ายงาน + ค้นจากชื่อ (Thai FTS + vector)
    ข้อมูลภายใน (ฝ่ายงาน รอบประเมิน หมายเหตุ ประเด็นข้อมูล) เห็นเฉพาะเจ้าหน้าที่"""
    internal = user.role in authz.INTERNAL_ROLES
    cols = _PUBLIC_COLS + (_INTERNAL_COLS if internal else "")
    where = ["status <> 'GROUP'"]
    params: dict = {}
    if doc_type:
        where.append("doc_type = :doc_type"); params["doc_type"] = doc_type
    if status:
        where.append("status = :status"); params["status"] = status
    if section:
        where.append(":section = ANY(authority_secs)"); params["section"] = section
    if unit and internal:
        where.append("owner_unit ILIKE :unit"); params["unit"] = f"%{unit}%"
    if issues_only and internal:
        where.append("cardinality(issues) > 0")
    order = "ORDER BY group_no::text ~ '^[0-9]+$' DESC, NULLIF(regexp_replace(group_no, '[^0-9]', '', 'g'), '')::int, sheet_row"
    rank: dict[int, float] = {}
    if q and q.strip():
        res = hybrid_search(conn, user, q, kinds=["register"], as_of=None, limit=120)
        good = [h for h in res.hits if h.coverage >= 0.35 or h.exact_ref or h.cosine >= 0.6]
        good = good or [h for h in res.hits if h.coverage > 0][:30]  # คำค้นบางคำไม่อยู่ในทะเบียน: ผ่อนเกณฑ์
        # ชื่อที่มีคำค้นทั้งวลี (ไม่สนช่องว่าง) ขึ้นก่อน แล้วตามลำดับ hybrid
        needle = re.sub(r"\s+", "", q)
        phrase = {r["register_id"] for r in rows(conn, """SELECT register_id FROM law_register
                   WHERE strpos(regexp_replace(title_th, '\\s', '', 'g'), :n) > 0""", n=needle)}
        hyb = [h.ref_id for h in res.hits]
        ordered = sorted(phrase, key=lambda i: hyb.index(i) if i in hyb else len(hyb))
        ordered += [h.ref_id for h in good if h.ref_id not in phrase]
        rank = {rid: i for i, rid in enumerate(ordered)}
        if not rank:
            return {"items": [], "total": 0, "facets": _register_facets(conn, internal)}
        where.append("register_id = ANY(:ids)"); params["ids"] = list(rank)
    sql_where = " AND ".join(where)
    total = conn.execute(text(f"SELECT count(*) FROM law_register WHERE {sql_where}"), params).scalar()
    items = rows(conn, f"SELECT {cols} FROM law_register WHERE {sql_where} {order}", **params)
    if rank:
        items.sort(key=lambda r: rank[r["register_id"]])
    items = items[offset: offset + limit]
    return {"items": items, "total": total, "facets": _register_facets(conn, internal)}


def _register_facets(conn: Connection, internal: bool) -> dict:
    f = {
        "doc_types": rows(conn, """SELECT doc_type AS key, count(*) AS n FROM law_register
                                   WHERE status <> 'GROUP' AND doc_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"""),
        "statuses": rows(conn, """SELECT status AS key, count(*) AS n FROM law_register
                                  WHERE status <> 'GROUP' GROUP BY 1 ORDER BY 2 DESC"""),
        "sections": rows(conn, """SELECT s AS key, count(*) AS n FROM law_register, unnest(authority_secs) s
                                  GROUP BY 1 ORDER BY 2 DESC, 1"""),
        "meta": one(conn, """SELECT count(*) FILTER (WHERE status <> 'GROUP') AS total,
                                    count(DISTINCT group_no) AS groups, max(source_file) AS source_file,
                                    max(imported_at) AS imported_at FROM law_register"""),
    }
    if internal:
        f["units"] = rows(conn, """SELECT u AS key, count(*) AS n FROM law_register,
                                   unnest(string_to_array(owner_unit, '/')) u WHERE owner_unit IS NOT NULL
                                   GROUP BY 1 ORDER BY 2 DESC""")
        f["meta"]["with_issues"] = conn.execute(
            text("SELECT count(*) FROM law_register WHERE cardinality(issues) > 0")).scalar()
    return f


@router.get("/register/{register_id}")
def get_register(register_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    internal = user.role in authz.INTERNAL_ROLES
    cols = _PUBLIC_COLS + (_INTERNAL_COLS if internal else "")
    item = one(conn, f"SELECT {cols} FROM law_register WHERE register_id = :i", i=register_id)
    if not item or item["status"] == "GROUP":
        raise HTTPException(404, detail={"code": "NOT_FOUND", "detail": "ไม่พบระเบียนนี้"})
    # ระเบียนในกลุ่มเดียวกัน (ฉบับแก้ไข/ฉบับที่ออกตามกัน) เรียงตามแถวในทะเบียน
    related = rows(conn, f"""SELECT register_id, reg_no, doc_type, title_th, effective_text, effective_date, status
                             FROM law_register WHERE group_no = :g AND register_id <> :i AND status <> 'GROUP'
                             ORDER BY sheet_row""", g=item["group_no"], i=register_id)
    heading = one(conn, "SELECT title_th FROM law_register WHERE group_no = :g AND status = 'GROUP' LIMIT 1",
                  g=item["group_no"])
    first = one(conn, """SELECT p.eli_id FROM provision p JOIN legal_instrument i USING (instrument_id)
                         WHERE i.eli_id = :e ORDER BY p.sort_order LIMIT 1""", e=item["instrument_eli"]) \
        if item["instrument_eli"] else None
    audit.record(conn, user, "law.register.view", str(register_id))
    return {"item": item, "related": related, "group_title": heading["title_th"] if heading else None,
            "first_provision_eli": first["eli_id"] if first else None}
