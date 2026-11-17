import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..db import get_conn, one, rows
from ..search.hybrid import hybrid_search
from ..search.thai import content_terms, normalize, tokenize
from ..rawfiles import sources_of
from . import coverage as law_coverage
from .resolver import NotYetInForce, Repealed, resolve

STATUS_TH = {"IN_FORCE": "ใช้บังคับ", "REPEALED": "ยกเลิก", "UNKNOWN": "ไม่ระบุ", "GROUP": "หัวกลุ่ม"}

router = APIRouter(prefix="/api/v1/laws", tags=["laws"])

# ทะเบียนกฎหมายลำดับรองของ กกพ. ระบุบทอาศัยอำนาจเป็นเลขมาตราของ พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550
PARENT_ACT = "eli/th/act/2550/energy-industry"
_SEC = re.compile(rf"^{re.escape(PARENT_ACT)}/sec-(\d+)$")


# ลำดับตราสาร: พ.ร.บ.การประกอบกิจการพลังงาน (ฉบับหลักของระบบ) → ระเบียบ/ประกาศ กกพ. → กฎหมายกลางอื่น
INSTRUMENT_RANK_SQL = f"""(CASE WHEN {{a}}eli_id = '{PARENT_ACT}' THEN 0
                               WHEN {{a}}eli_id LIKE 'eli/th/reg/erc/%' OR {{a}}eli_id LIKE 'eli/th/ann/erc/%' THEN 1
                               ELSE 2 END)"""
PROV_REF = re.compile(r"(มาตรา|ข้อ)\s*(\d+(?:/\d+)?)")


def _parent_section(eli: str) -> int | None:
    m = _SEC.match(eli)
    return int(m.group(1)) if m else None


def _subordinate_counts(conn: Connection) -> dict[int, int]:
    return {r["sec"]: r["n"] for r in rows(conn, """
        SELECT s AS sec, count(*) AS n FROM law_register, unnest(authority_secs) s
        WHERE status <> 'GROUP' GROUP BY s""")}


def subordinate_of(conn: Connection, sec: int) -> list[dict]:
    """กฎหมายลำดับรองที่ออกโดยอาศัยอำนาจตามมาตรา sec (จากทะเบียน) พร้อมลิงก์ตัวบทเต็มถ้าโหลดแล้ว"""
    return rows(conn, """
        SELECT r.register_id, r.reg_no, r.doc_type, r.title_th, r.authority_text, r.effective_text, r.effective_date,
               r.status, r.status_assumed, r.instrument_eli, g.title_th AS group_title,
               (SELECT p.eli_id FROM provision p JOIN legal_instrument i USING (instrument_id)
                 WHERE i.eli_id = r.instrument_eli ORDER BY p.sort_order LIMIT 1) AS first_provision_eli
        FROM law_register r
        LEFT JOIN law_register g ON g.group_no = r.group_no AND g.status = 'GROUP'
        WHERE :sec = ANY(r.authority_secs) AND r.status <> 'GROUP'
        ORDER BY (r.status = 'REPEALED'), r.effective_date DESC NULLS LAST, r.sheet_row""", sec=sec)


def parent_authority(conn: Connection, instrument_eli: str) -> dict | None:
    """บทอาศัยอำนาจของตราสารลำดับรอง (จากทะเบียน) → ลิงก์ไปมาตราของ พ.ร.บ.แม่บท"""
    r = one(conn, """SELECT register_id, reg_no, authority_text, authority_secs FROM law_register
                     WHERE instrument_eli = :e AND status <> 'GROUP' ORDER BY sheet_row LIMIT 1""", e=instrument_eli)
    if not r:
        return None
    secs = r["authority_secs"] or []
    loaded = {x["eli_id"] for x in rows(conn, "SELECT eli_id FROM provision WHERE eli_id = ANY(:e)",
                                        e=[f"{PARENT_ACT}/sec-{n}" for n in secs])}
    return {"register_id": r["register_id"], "reg_no": r["reg_no"], "authority_text": r["authority_text"],
            "sections": [{"no": n, "eli": f"{PARENT_ACT}/sec-{n}" if f"{PARENT_ACT}/sec-{n}" in loaded else None}
                         for n in secs]}


@router.get("/instruments")
def list_instruments(category: str | None = None, conn: Connection = Depends(get_conn)):
    """สารบัญตราสารและรายมาตรา พร้อมสรุปของรุ่นปัจจุบัน"""
    insts = rows(conn, """SELECT instrument_id, eli_id, instrument_type, title_th, short_title, issuer, category,
                                 promulgated_date, effective_date, is_demo
                          FROM legal_instrument
                          WHERE (CAST(:cat AS text) IS NULL OR category = :cat)
                          ORDER BY """ + INSTRUMENT_RANK_SQL.format(a="") + """, promulgated_date""", cat=category)
    provs = rows(conn, """
        SELECT p.instrument_id, p.eli_id, p.provision_no, p.part_heading,
               count(pv.*) FILTER (WHERE pv.status = 'approved') AS version_count,
               (SELECT summary FROM provision_version x
                 WHERE x.provision_id = p.provision_id AND x.status = 'approved'
                 ORDER BY x.valid_from DESC LIMIT 1) AS latest_summary
        FROM provision p LEFT JOIN provision_version pv USING (provision_id)
        GROUP BY p.provision_id ORDER BY p.instrument_id, p.sort_order""")
    counts = _subordinate_counts(conn)
    by_inst: dict[int, list] = {}
    for p in provs:
        sec = _parent_section(p["eli_id"])
        p["subordinate_count"] = counts.get(sec, 0) if sec is not None else 0
        by_inst.setdefault(p.pop("instrument_id"), []).append(p)
    for i in insts:
        i["provisions"] = by_inst.get(i["instrument_id"], [])
        i["subordinate_total"] = one(conn, """SELECT count(*) AS n FROM law_register
                                              WHERE cardinality(authority_secs) > 0 AND status <> 'GROUP'""")["n"] \
            if i["eli_id"] == PARENT_ACT else 0
    categories = [r["category"] for r in rows(conn, "SELECT DISTINCT category FROM legal_instrument ORDER BY 1")]
    return {"items": insts, "categories": categories}


@router.get("/search")
def search_laws(q: str, as_of: date | None = None, user: User = Depends(current_user),
                conn: Connection = Depends(get_conn)):
    """ค้นตัวบท (hybrid) เฉพาะรุ่นที่มีผล ณ วันที่ที่เลือก"""
    as_of = as_of or date.today()
    res = hybrid_search(conn, user, q, kinds=["provision"], as_of=as_of, limit=20)
    exact = _exact_provisions(conn, user, q, as_of)
    seen = {h["chunk_id"] for h in exact}
    return {"items": exact + [h for h in res.hits if h.chunk_id not in seen]}


def _exact_provisions(conn: Connection, user: User, q: str, as_of: date) -> list[dict]:
    """ค้นด้วยเลขมาตรา/ข้อ (เช่น "มาตรา 11") → ทุกฉบับที่มีมาตรานั้น เรียงฉบับที่ชื่อตรงคำค้นก่อน แล้ว พ.ร.บ.พลังงาน → ระเบียบ กกพ. → อื่น ๆ"""
    qn = normalize(q)
    m = PROV_REF.search(qn)
    if not m:
        return []
    perm, params = authz.search_filter_sql(user)
    found = rows(conn, f"""
        SELECT s.chunk_id, s.kind, s.ref_id, s.title, s.chunk_text, s.eli_id, s.version_no, s.valid_from, s.valid_to,
               s.classification, i.title_th AS instrument_title, {INSTRUMENT_RANK_SQL.format(a="i.")} AS rank
        FROM search_chunk s JOIN provision p ON p.eli_id = s.eli_id JOIN legal_instrument i USING (instrument_id)
        WHERE s.kind = 'provision' AND p.provision_no = :pno AND i.is_demo = FALSE
          AND s.valid_from <= :d AND (s.valid_to IS NULL OR :d <= s.valid_to) AND {{perm}}""".replace("{perm}", perm),
        pno=f"{m.group(1)} {m.group(2)}", d=as_of, **params)
    rest = content_terms(PROV_REF.sub(" ", qn))
    for f in found:
        f["title_overlap"] = len(rest & set(tokenize(f["instrument_title"]))) if rest else 0
        f["exact_ref"] = True
    found.sort(key=lambda f: (-f["title_overlap"], f["rank"], f["instrument_title"]))
    return found


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
               a.appeal_no AS ref_no, a.subject, a.incident_date, a.decision_result AS result, a.is_demo,
               a.classification, ARRAY(SELECT user_id FROM appeal_owner o WHERE o.appeal_id = a.appeal_id) AS owners
        FROM citation_link c JOIN appeal a ON c.owner_type = 'appeal' AND a.appeal_id = c.owner_id
        WHERE c.cited_eli_id = :eli AND c.state = 'verified'
        UNION ALL
        SELECT c.cited_version_no, c.owner_type, c.owner_id, c.note,
               coalesce(k.red_no, k.black_no), k.subject, k.incident_date, k.result, k.is_demo,
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

    sec = _parent_section(eli_id)
    subordinate = subordinate_of(conn, sec) if sec is not None else []

    audit.record(conn, user, "law.view", eli_id, detail={"as_of": as_of.isoformat(), "version": resolved})
    return {
        "provision": prov, "as_of": as_of, "resolved_version_no": resolved, "problem": problem,
        "versions": versions, "citations": visible, "citations_hidden": hidden,
        "subordinate": subordinate, "parent_authority": parent_authority(conn, prov["instrument_eli"]),
        "source_files": sources_of(conn, prov["instrument_eli"], user),
    }


# ---------- ทะเบียนคุมกฎหมายลำดับรอง ----------
_PUBLIC_COLS = ("register_id, reg_no, group_no, doc_type, title_th, authority_text, authority_secs, effective_text, "
                "effective_date, status, status_assumed, instrument_eli")
_INTERNAL_COLS = ", owner_unit, review_text, status_text, note, hearing_text, attachments, issues, sheet_row, source_file, imported_at"


@router.get("/register")
def list_register(q: str | None = None, doc_type: str | None = None, status: str | None = None,
                  section: int | None = None, unit: str | None = None, issues_only: bool = False,
                  has_text: bool | None = None,
                  limit: int = Query(default=50, le=400), offset: int = 0,
                  user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ทะเบียนกฎหมายลำดับรอง: กรองตามประเภท/สถานะ/มาตราแม่บท/ฝ่ายงาน + ค้นจากชื่อ (Thai FTS + vector)
    ข้อมูลภายใน (ฝ่ายงาน รอบประเมิน หมายเหตุ ประเด็นข้อมูล) เห็นเฉพาะเจ้าหน้าที่"""
    internal = user.role in authz.INTERNAL_ROLES
    cols = _PUBLIC_COLS + (_INTERNAL_COLS if internal else "")
    where, params = _register_where(internal, doc_type, status, section, unit, issues_only, has_text)
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


def _register_where(internal: bool, doc_type=None, status=None, section=None, unit=None, issues_only=False,
                    has_text=None, alias: str = "") -> tuple[list[str], dict]:
    a = f"{alias}." if alias else ""
    where = [f"{a}status <> 'GROUP'"]
    params: dict = {}
    if doc_type:
        where.append(f"{a}doc_type = :doc_type"); params["doc_type"] = doc_type
    if status:
        where.append(f"{a}status = :status"); params["status"] = status
    if section:
        where.append(f":section = ANY({a}authority_secs)"); params["section"] = section
    if unit and internal:
        where.append(f"{a}owner_unit ILIKE :unit"); params["unit"] = f"%{unit}%"
    if issues_only and internal:
        where.append(f"cardinality({a}issues) > 0")
    if has_text is not None:
        where.append(f"{a}instrument_eli IS {'NOT ' if has_text else ''}NULL")
    return where, params


@router.get("/coverage")
def coverage(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """นำเข้าตัวบทแล้วกี่ฉบับ ทะเบียนมีตัวบทเต็มกี่ฉบับ ประเด็นค้าง และบันทึกการนำเข้า (เจ้าหน้าที่เท่านั้น)"""
    authz.require_internal(user)
    return law_coverage.summary(conn)


@router.get("/register/export")
def export_register(doc_type: str | None = None, status: str | None = None, section: int | None = None,
                    has_text: bool | None = False, user: User = Depends(current_user),
                    conn: Connection = Depends(get_conn)):
    """ส่งออกรายการทะเบียนตามตัวกรองเป็น Excel — ค่าเริ่มต้นคือรายการที่ยังไม่มีตัวบทในระบบ (ใช้ขอไฟล์จากฝ่ายกฎหมาย)"""
    authz.require_internal(user)
    where, params = _register_where(True, doc_type, status, section, None, False, has_text, alias="r")
    data = law_coverage.missing_workbook(conn, " AND ".join(where), params)
    audit.record(conn, user, "law.register.export", "law_register",
                 detail={"has_text": has_text, "status": status, "section": section, "doc_type": doc_type})
    name = f"register-{'missing-text' if has_text is False else 'all'}-{date.today().isoformat()}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _register_facets(conn: Connection, internal: bool) -> dict:
    f = {
        "doc_types": rows(conn, """SELECT doc_type AS key, count(*) AS n FROM law_register
                                   WHERE status <> 'GROUP' AND doc_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"""),
        "statuses": rows(conn, """SELECT status AS key, count(*) AS n, count(*) FILTER (WHERE status_assumed) AS assumed
                                  FROM law_register WHERE status <> 'GROUP' GROUP BY 1 ORDER BY 2 DESC"""),
        "sections": rows(conn, """SELECT s AS key, count(*) AS n FROM law_register, unnest(authority_secs) s
                                  GROUP BY 1 ORDER BY 2 DESC, 1"""),
        "meta": one(conn, """SELECT count(*) FILTER (WHERE status <> 'GROUP') AS total,
                                    count(*) FILTER (WHERE status <> 'GROUP' AND instrument_eli IS NOT NULL) AS with_text,
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
    related = rows(conn, f"""SELECT register_id, reg_no, doc_type, title_th, effective_text, effective_date, status, status_assumed
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
