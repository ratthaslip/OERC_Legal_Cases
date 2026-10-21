from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..db import get_conn, one, rows
from ..search.hybrid import hybrid_search
from .resolver import NotYetInForce, Repealed, resolve

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
