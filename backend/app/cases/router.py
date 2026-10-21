from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..db import get_conn, one, rows
from ..law.lookup import provision_labels
from ..search.hybrid import hybrid_search

router = APIRouter(prefix="/api/v1/court-cases", tags=["court-cases"])

ACCESS_SQL = "(k.classification = 'internal' OR EXISTS (SELECT 1 FROM court_case_owner o WHERE o.case_id = k.case_id AND o.user_id = :uid))"


@router.get("")
def list_cases(q: str | None = None, court_level: str | None = None, sector: str | None = None,
               user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    base = rows(conn, f"""
        SELECT k.case_id, k.black_no, k.red_no, k.court_level, k.case_category, k.subject, k.energy_sector,
               k.incident_date, k.status, k.stage_text, k.result, k.classification, a.appeal_no AS origin_appeal_no
        FROM court_case k LEFT JOIN appeal a ON a.appeal_id = k.appeal_id
        WHERE {ACCESS_SQL}
          AND (CAST(:lvl AS text) IS NULL OR k.court_level = :lvl)
          AND (CAST(:sector AS text) IS NULL OR k.energy_sector = :sector)
        ORDER BY k.filed_date DESC NULLS LAST""", uid=user.user_id, lvl=court_level, sector=sector)
    omitted = 0
    if q and q.strip():
        res = hybrid_search(conn, user, q, kinds=["court_case"], limit=200, sector=sector)
        exact = [h for h in res.hits if h.exact_ref]
        # ค้นด้วยเลขที่แล้วเจอตรงตัว → แสดงเฉพาะเรื่องนั้น ไม่ปนผลที่คล้ายกัน
        order = ({h.ref_id: i for i, h in enumerate(exact)} if exact else
                 {h.ref_id: i for i, h in enumerate(res.hits) if h.lexical or h.cosine >= 0.3})
        base = sorted([k for k in base if k["case_id"] in order], key=lambda k: order[k["case_id"]])
        omitted = res.restricted_omitted
    levels = [r["court_level"] for r in rows(conn, "SELECT DISTINCT court_level FROM court_case ORDER BY 1")]
    sectors = [r["energy_sector"] for r in rows(conn, "SELECT DISTINCT energy_sector FROM court_case ORDER BY 1")]
    return {"items": base, "restricted_omitted": omitted, "court_levels": levels, "sectors": sectors}


@router.get("/{case_id}")
def get_case(case_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_internal(user)
    k = one(conn, """SELECT k.*, a.appeal_no AS origin_appeal_no, a.classification AS origin_classification
                     FROM court_case k LEFT JOIN appeal a ON a.appeal_id = k.appeal_id WHERE k.case_id = :i""", i=case_id)
    if not k:
        raise authz.not_found()
    owners = [r["user_id"] for r in rows(conn, "SELECT user_id FROM court_case_owner WHERE case_id = :i", i=case_id)]
    if not authz.can_read_case(user, k["classification"], owners):
        audit.record(conn, user, "court_case.view", f"court_case/{case_id}", "DENY")
        raise authz.not_found()
    # ลิงก์ไปเรื่องอุทธรณ์ต้นทางแสดงเฉพาะเมื่อผู้ใช้มีสิทธิ์อ่านเรื่องนั้นด้วย
    if k["appeal_id"]:
        a_owners = [r["user_id"] for r in rows(conn, "SELECT user_id FROM appeal_owner WHERE appeal_id = :i",
                                               i=k["appeal_id"])]
        if not authz.can_read_case(user, k["origin_classification"], a_owners):
            k["appeal_id"], k["origin_appeal_no"] = None, None
    k.pop("origin_classification", None)
    proceedings = rows(conn, """SELECT proceeding_date, title, detail FROM court_proceeding
                                WHERE case_id = :i ORDER BY proceeding_date""", i=case_id)
    docs = rows(conn, """SELECT document_id, doc_no, doc_type, doc_date, pages, excerpt FROM case_document
                         WHERE owner_type = 'court_case' AND owner_id = :i ORDER BY doc_date""", i=case_id)
    cits = rows(conn, """SELECT cited_eli_id, cited_version_no, note, state FROM citation_link
                         WHERE owner_type = 'court_case' AND owner_id = :i""", i=case_id)
    labels = provision_labels(conn, [c["cited_eli_id"] for c in cits])
    for c in cits:
        c["label"] = labels.get(c["cited_eli_id"], {}).get("label", c["cited_eli_id"])
    audit.record(conn, user, "court_case.view", f"court_case/{case_id}", detail={"classification": k["classification"]})
    return {"case": {**k, "owners": owners}, "proceedings": proceedings, "documents": docs, "citations": cits}
