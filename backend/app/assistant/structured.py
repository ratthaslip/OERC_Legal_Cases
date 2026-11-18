"""ระดับ 2: ตอบคำถามเชิงโครงสร้างด้วย query ตรง แทนการค้นหาความคล้าย

คำถามประเภทนี้ไม่ได้ขึ้นกับว่าข้อความใด "คล้าย" คำถาม แต่ขึ้นกับความสัมพันธ์ที่บันทึกไว้ การค้นหาความคล้าย
จึงดึงมาไม่ครบหรือผิดฉบับ (ดูชุดทดสอบ G61–G75) ระบบจึงจับรูปแบบคำถามแล้วรัน query ที่ให้ผลครบและนับถูกเสมอ

  subordinate  — "ระเบียบ/ประกาศที่ออกโดยอาศัยอำนาจตามมาตรา N มีฉบับใดบ้าง / กี่ฉบับ"   (ทะเบียน → มาตรา)
  parent       — "<ชื่อระเบียบ> ออกโดยอาศัยอำนาจตามมาตราใด"                        (ตราสาร → มาตรา)
  amendments   — "มาตรา/ข้อ N ของ <ฉบับ> ถูกแก้ไขโดยฉบับใด / มีกี่รุ่น"                 (รุ่นของบทบัญญัติ)
  definition   — "คำว่า X (ตาม <ฉบับ>) หมายความว่าอะไร"                                (บทนิยาม)

คำตอบสร้างจากแม่แบบ (ไม่ผ่าน LLM) ทุกประโยคอ้างแหล่ง และยังผ่านตัวตรวจอ้างอิงทีละประโยคเหมือนคำตอบปกติ
ถ้าจับรูปแบบได้แต่ระบุเป้าหมายไม่ได้ชัดเจน จะคืน None ให้ใช้การค้นหาปกติแทน (ไม่เดา)
"""
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import authz
from ..auth import User
from ..db import one, rows
from ..law import relations
from ..law.router import PARENT_ACT, subordinate_of
from ..search.hybrid import Hit, hybrid_search
from ..search.thai import content_terms, normalize, tokenize

TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
ACT_TITLE = "พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550"
LIST_MAX = 10

OTHER_ACTS = re.compile(r"วิธีปฏิบัติราชการ|ศาลปกครอง|วิธีพิจารณาคดีปกครอง")
SUB_Q = re.compile(r"(?:ออก|อาศัยอำนาจ)\S{0,24}?มาตรา\s*(\d+)")
SUB_Q2 = re.compile(r"มาตรา\s*(\d+)\S{0,12}(?:มี)?(?:กฎหมายลำดับรอง|กฎหมายรอง)")
LIST_Q = re.compile(r"บ้าง|กี่ฉบับ|จำนวน|ฉบับใด|ฉบับไหน|มีกี่|รายการ|ทั้งหมด|อะไร")
COUNT_Q = re.compile(r"กี่ฉบับ|จำนวน|มีกี่")
IN_FORCE_Q = re.compile(r"ยังใช้บังคับ|ใช้บังคับอยู่|ยังมีผล|มีผลใช้บังคับอยู่|ที่ใช้บังคับ")
REPEALED_Q = re.compile(r"ยกเลิก")
PARENT_Q = re.compile(r"(?:อาศัยอำนาจ|ออกตาม|ออกโดย)\S{0,8}?(?:มาตรา|บทบัญญัติ)\s*(?:ใด|ไหน|อะไร)|บทอาศัยอำนาจ(?:คือ|ของ|ได้แก่)")
AMEND_Q = re.compile(r"แก้ไข|ฉบับแก้|ประวัติ|กี่รุ่น|รุ่นใด|เปลี่ยนแปลง|เพิ่มเติมโดย")
PROV_REF = re.compile(r"(มาตรา|ข้อ)\s*(\d+(?:/\d+)?)")
DEF_Q = re.compile(r"หมายถึง|หมายความว่า|คืออะไร|นิยาม|ความหมาย")
DEF_LEAD = re.compile(r"^\s*(?:คำว่า|ความหมายของ(?:คำว่า)?|นิยามของ(?:คำว่า)?|นิยาม)\s*[“\"]?")
DEF_RE = re.compile(r"“([^”\n]{1,60})”\s*หมายความว่า\s*(.*?)(?=\n?\s*“[^”\n]{1,60}”\s*หมายความว่า|\Z)", re.S)
AMENDING = re.compile(r"ให้เพิ่มความต่อไปนี้|ให้ยกเลิกความใน")
DOC_TYPE_WORDS = ["ประกาศสำนักงาน กกพ.", "ระเบียบสำนักงาน กกพ.", "ประกาศกระทรวงพลังงาน", "ข้อบังคับ", "กฎบัตร",
                  "ประกาศ", "ระเบียบ", "คำสั่ง"]
STATUS_TH = {"IN_FORCE": "ใช้บังคับ", "REPEALED": "ยกเลิก", "UNKNOWN": "ไม่ระบุสถานะ"}


def th_date(d: date | None) -> str:
    return f"{d.day} {TH_MONTHS[d.month - 1]} {d.year + 543}" if d else "—"


@dataclass
class StructuredAnswer:
    route: str
    hits: list[Hit]
    sentences: list[tuple[str, list[int]]] = field(default_factory=list)   # (ข้อความ, ดัชนีของ hits ที่อ้าง)


def _hit(r: dict, **over) -> Hit:
    base = {k: r.get(k) for k in Hit.__dataclass_fields__ if k in r}
    base.update(over)
    for k in ("chunk_id", "kind", "ref_id", "title", "chunk_text", "eli_id", "version_no", "valid_from", "valid_to",
              "classification"):
        base.setdefault(k, None)
    base["classification"] = base["classification"] or "public"
    return Hit(**base)


def _chunk(conn: Connection, user: User, kind: str, ref_id: int) -> Hit | None:
    perm, params = authz.search_filter_sql(user)
    r = one(conn, f"""SELECT chunk_id, kind, ref_id, title, chunk_text, eli_id, version_no, valid_from, valid_to,
                             classification FROM search_chunk WHERE kind = :k AND ref_id = :r AND {perm} LIMIT 1""",
            k=kind, r=ref_id, **params)
    return _hit(r) if r else None


def _provision_at(conn: Connection, user: User, eli: str, as_of: date) -> Hit | None:
    perm, params = authz.search_filter_sql(user)
    r = one(conn, f"""SELECT chunk_id, kind, ref_id, title, chunk_text, eli_id, version_no, valid_from, valid_to,
                             classification FROM search_chunk
                      WHERE kind = 'provision' AND eli_id = :e AND valid_from <= :d
                        AND (valid_to IS NULL OR :d <= valid_to) AND {perm} LIMIT 1""",
            e=eli, d=as_of, **params)
    return _hit(r) if r else None


def _overlap(q_terms: set[str], title: str) -> float:
    t = set(tokenize(title))
    return len(q_terms & t) / (len(q_terms) or 1)


def _phrase_score(q: str, title: str) -> tuple[float, int, float]:
    """(สัดส่วนคำที่ตรง, จำนวนคู่คำติดกันที่ตรง) — คู่คำแยกชื่อฉบับที่มีคำเหมือนกันแต่เรียงต่างกันได้
    เช่น "ข้อพิพาทระหว่างผู้รับใบอนุญาต" vs "ข้อพิพาทระหว่างผู้ใช้พลังงานกับผู้รับใบอนุญาต"
    """
    title = re.split(r"\s*(?:ตามมาตรา|แห่งพระราชบัญญัติ)", title)[0]  # ส่วนอ้าง พ.ร.บ. แม่บทไม่ใช่ชื่อเรื่อง
    title = re.sub(r"พ\s*\.\s*ศ\s*\.\s*[๐-๙\d]{4}", " ", title)
    qt, tt = tokenize(q), tokenize(title)
    qb, tb = set(zip(qt, qt[1:])), set(zip(tt, tt[1:]))
    t_terms = content_terms(title)
    precision = len(t_terms & set(qt)) / len(t_terms) if t_terms else 0.0  # ชื่อที่ไม่มีคำเกินจากคำถามได้เปรียบ
    return _overlap(content_terms(q), title), len(qb & tb), round(precision, 2)


# ---------------------------------------------------------------- subordinate
def _doc_type_filter(q: str) -> str | None:
    head = re.split(r"ที่ออก|ซึ่งออก|ออกโดย|ที่อาศัย", q)[0]
    if re.search(r"กฎหมายลำดับรอง|กฎหมายรอง", head):
        return None
    for w in DOC_TYPE_WORDS:
        if w in head:
            return w
    return None


def route_subordinate(conn: Connection, user: User, q: str, as_of: date) -> StructuredAnswer | None:
    m = SUB_Q.search(q) or SUB_Q2.search(q)
    if not m or not LIST_Q.search(q) or OTHER_ACTS.search(q) or PARENT_Q.search(q):
        return None
    sec = int(m.group(1))
    all_items = [i for i in subordinate_of(conn, sec) if not i["effective_date"] or i["effective_date"] <= as_of]
    dtype = _doc_type_filter(q)
    status = "IN_FORCE" if IN_FORCE_Q.search(q) else "REPEALED" if REPEALED_Q.search(q) else None
    items = [i for i in all_items if (not dtype or i["doc_type"] == dtype) and (not status or i["status"] == status)]
    label = dtype or "กฎหมายลำดับรอง"
    scope = {"IN_FORCE": "ที่ยังใช้บังคับ", "REPEALED": "ที่ยกเลิกแล้ว"}.get(status, "")

    # แหล่งรวม: รายการทั้งหมดของมาตรานี้จากทะเบียน (หลักฐานของจำนวนที่นับ) — คลิกแล้วเปิดหน้ามาตรานั้น
    by: dict[tuple, int] = {}
    for i in all_items:
        by[(i["doc_type"] or "อื่น ๆ", i["status"])] = by.get((i["doc_type"] or "อื่น ๆ", i["status"]), 0) + 1
    lines = [f"{t} {STATUS_TH.get(s, s)} {n} ฉบับ" for (t, s), n in sorted(by.items())]
    listing = "\n".join(f"#{i['reg_no']} {i['title_th']} ({i['doc_type']}, {STATUS_TH.get(i['status'], i['status'])}, "
                        f"ใช้บังคับ {th_date(i['effective_date'])})" for i in all_items)
    summary = _hit({
        "chunk_id": -sec, "kind": "register_list", "ref_id": sec, "eli_id": f"{PARENT_ACT}/sec-{sec}",
        "title": f"ทะเบียนกฎหมายลำดับรอง: รายการที่ออกโดยอาศัยอำนาจตามมาตรา {sec} แห่ง {ACT_TITLE}",
        "chunk_text": f"ทั้งหมด {len(all_items)} ฉบับ: " + "; ".join(lines) + "\n" + listing,
    })
    hits = [summary]
    n_in = sum(i["status"] == "IN_FORCE" for i in items)
    n_rep = sum(i["status"] == "REPEALED" for i in items)
    breakdown = f" (ใช้บังคับ {n_in} ยกเลิก {n_rep})" if not status and items else ""
    sents = [(f"ทะเบียนกฎหมายลำดับรองระบุ{label}{scope}ที่ออกโดยอาศัยอำนาจตามมาตรา {sec} แห่ง {ACT_TITLE} "
              f"จำนวน {len(items)} ฉบับ{breakdown}", [0])]
    if dtype:
        others = {}
        for i in all_items:
            if i["doc_type"] != dtype and (not status or i["status"] == status):
                others[i["doc_type"] or "อื่น ๆ"] = others.get(i["doc_type"] or "อื่น ๆ", 0) + 1
        if others:
            sents.append(("นอกจากนี้มี " + " ".join(f"{t} {n} ฉบับ" for t, n in others.items()) +
                          f" ที่ออกตามมาตรา {sec} เช่นกัน", [0]))
    if items and not (COUNT_Q.search(q) and len(items) > LIST_MAX):
        for n, i in enumerate(items[:LIST_MAX], 1):
            h = _chunk(conn, user, "register", i["register_id"])
            if not h:
                continue
            hits.append(h)
            sents.append((f"{n}. {i['title_th']} (ทะเบียน #{i['reg_no']} · {STATUS_TH.get(i['status'], i['status'])} · "
                          f"ใช้บังคับ {th_date(i['effective_date'])})", [len(hits) - 1]))
    if len(items) > LIST_MAX or (COUNT_Q.search(q) and items):
        sents.append((f"รายการทั้งหมดดูได้ที่หน้าตัวบทมาตรา {sec} แผงกฎหมายลำดับรอง", [0]))
    return StructuredAnswer("subordinate", hits, sents)


# ---------------------------------------------------------------- parent authority
def route_parent(conn: Connection, user: User, q: str, as_of: date) -> StructuredAnswer | None:
    if not PARENT_Q.search(q):
        return None
    target = PARENT_Q.split(q)[0].strip() or q
    res = hybrid_search(conn, user, target, kinds=["register"], as_of=as_of, limit=5)
    top = next((h for h in res.hits if h.coverage >= 0.5), None)
    if not top:
        return None
    reg = one(conn, "SELECT reg_no, title_th, authority_text FROM law_register WHERE register_id = :i", i=top.ref_id)
    secs = [int(e.rsplit("-", 1)[-1]) for e in relations.authority_sections(conn, register_id=top.ref_id)]
    if not reg or not secs:
        return None
    hits = [top]
    secs_txt = ", ".join(f"มาตรา {s}" for s in secs)
    sents = [(f"{reg['title_th']} ออกโดยอาศัยอำนาจตาม{secs_txt} แห่ง {ACT_TITLE} (ทะเบียน #{reg['reg_no']})", [0])]
    for s in secs[:3]:
        h = _provision_at(conn, user, f"{PARENT_ACT}/sec-{s}", as_of)
        if not h:
            continue
        hits.append(h)
        body = re.sub(r"\s+", " ", h.chunk_text.split("\nสาระสำคัญ:")[0]).strip()
        excerpt = body if len(body) <= 260 else body[: body.rfind(" ", 0, 260)] + " …"
        sents.append((f"มาตรา {s} บัญญัติว่า {excerpt.removeprefix(f'มาตรา {s}').strip()}", [len(hits) - 1]))
    return StructuredAnswer("parent", hits, sents)


# ---------------------------------------------------------------- amendment history
def _pick_instrument(cands: list[dict], q: str) -> dict | None:
    scored = sorted(((_phrase_score(q, c["title_th"]), "amend" not in c["instrument_eli"], c) for c in cands),
                    key=lambda x: (x[0], x[1]), reverse=True)
    if not scored or scored[0][0][0] == 0:
        return None
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None  # กำกวม ไม่เดา (คะแนน + ฉบับหลัก/ฉบับแก้ไข เท่ากันทุกส่วน)
    return scored[0][2]


def route_amendments(conn: Connection, user: User, q: str, as_of: date) -> StructuredAnswer | None:
    m = PROV_REF.search(q)
    if not m or not AMEND_Q.search(q) or SUB_Q.search(q) and LIST_Q.search(q):
        return None
    pno = f"{m.group(1)} {m.group(2)}"
    cands = rows(conn, """SELECT p.provision_id, p.eli_id, p.provision_no, i.title_th, i.eli_id AS instrument_eli
                          FROM provision p JOIN legal_instrument i USING (instrument_id) WHERE p.provision_no = :n""",
                 n=pno)
    # ตัดคำที่ถามเรื่องการแก้ไขออกก่อนจับคู่ชื่อฉบับ ("แก้ไข" ไม่ใช่ชื่อเรื่อง แต่ตรงกับชื่อฉบับแก้ไขเพิ่มเติมได้)
    target = _pick_instrument(cands, AMEND_Q.sub(" ", PROV_REF.sub(" ", q)))
    if not target:
        return None
    vers = [v for v in rows(conn, """SELECT version_id, version_no, body_text, valid_from, valid_to, status, amended_by
                                     FROM provision_version WHERE provision_id = :p ORDER BY version_no""",
                            p=target["provision_id"]) if authz.can_read_law_version(user, v["status"])
            and v["status"] == "approved"]
    if not vers:
        return None
    hits = [_hit({"chunk_id": -v["version_id"], "kind": "provision", "ref_id": v["version_id"],
                  "eli_id": target["eli_id"], "version_no": v["version_no"], "valid_from": v["valid_from"],
                  "valid_to": v["valid_to"],
                  "title": f"{pno} {target['title_th']} (รุ่นที่ {v['version_no']})",
                  "chunk_text": f"{pno} {v['body_text']}\nที่มาของรุ่น: {v['amended_by'] or '-'}"}) for v in vers]
    if len(vers) == 1:
        v = vers[0]
        sents = [(f"{pno} {target['title_th']} มีรุ่นเดียว ยังไม่มีการแก้ไขในข้อมูลของระบบ ใช้บังคับตั้งแต่ "
                  f"{th_date(v['valid_from'])}", [0])]
    else:
        sents = [(f"{pno} {target['title_th']} มี {len(vers)} รุ่น", list(range(len(vers))))]
        for i, v in enumerate(vers):
            until = th_date(v["valid_to"]) if v["valid_to"] else "ปัจจุบัน"
            src = re.sub(r"\s+", " ", v["amended_by"] or "-")
            sents.append((f"รุ่นที่ {v['version_no']} มีผล {th_date(v['valid_from'])} ถึง {until} — {src}", [i]))
    return StructuredAnswer("amendments", hits, sents)


# ---------------------------------------------------------------- definitions
def route_definition(conn: Connection, user: User, q: str, as_of: date) -> StructuredAnswer | None:
    if not DEF_Q.search(q):
        return None
    perm, params = authz.search_filter_sql(user)
    chunks = rows(conn, f"""
        SELECT s.chunk_id, s.kind, s.ref_id, s.title, s.chunk_text, s.eli_id, s.version_no, s.valid_from, s.valid_to,
               s.classification, pv.body_text, i.title_th AS instrument_title, i.eli_id AS instrument_eli,
               p.provision_no
        FROM search_chunk s JOIN provision_version pv ON s.kind = 'provision' AND pv.version_id = s.ref_id
        JOIN provision p USING (provision_id) JOIN legal_instrument i USING (instrument_id)
        WHERE pv.body_text LIKE '%%หมายความว่า%%' AND s.valid_from <= :d AND (s.valid_to IS NULL OR :d <= s.valid_to)
          AND {perm}
          AND s.chunk_id = (SELECT min(chunk_id) FROM search_chunk s2 WHERE s2.kind = 'provision' AND s2.ref_id = s.ref_id)
        """, d=as_of, **params)  # 1 แถวต่อรุ่น (บทนิยามยาวมีหลายท่อน แต่ใช้ถ้อยคำเต็มจาก body_text อยู่แล้ว)
    defs = []  # (term, definition, chunk)
    for c in chunks:
        if AMENDING.search(c["body_text"]) or "amend" in c["instrument_eli"]:
            continue  # ฉบับแก้ไขซ้ำกับฉบับรวม — อ้างฉบับรวม
        for term, body in DEF_RE.findall(normalize(c["body_text"])):
            defs.append((term.strip(), re.sub(r"\s+", " ", body).strip(), c))
    qn = normalize(q)
    lead = DEF_LEAD.match(qn)
    start = lead.end() if lead else 0
    found = []
    for term, body, c in defs:
        pos = qn.find(term, start)
        if pos < 0:
            continue
        after = qn[pos + len(term):pos + len(term) + 14]
        near_verb = bool(re.match(r"[”\"\s]*(?:ตาม|หมายถึง|หมายความว่า|คือ|ใน|ของ|มี)", after))
        if pos - start <= 3 or near_verb:
            found.append((pos, -len(term), term, body, c))
    if not found:
        return None
    found.sort(key=lambda x: (x[0], x[1]))
    term = found[0][2]
    same = [f for f in found if f[2] == term]
    rest = qn.replace(term, " ", 1)  # ตัดเฉพาะคำที่ถาม ชื่อฉบับที่อาจมีคำเดียวกันยังอยู่
    if len(same) > 1:
        best = max(_phrase_score(rest, f[4]["instrument_title"]) for f in same)
        if best[0] > 0:
            same = [f for f in same if _phrase_score(rest, f[4]["instrument_title"]) == best]
    hits, sents = [], []
    for _, _, t, body, c in same[:3]:
        hits.append(_hit(c))
        d = body if len(body) <= 420 else body[: body.rfind(" ", 0, 420)] + " …"
        sents.append((f"ตาม{c['instrument_title']} {c['provision_no']} “{t}” หมายความว่า {d}", [len(hits) - 1]))
    return StructuredAnswer("definition", hits, sents)


ROUTES = (route_subordinate, route_parent, route_amendments, route_definition)


def try_answer(conn: Connection, user: User, question: str, as_of: date) -> StructuredAnswer | None:
    q = normalize(question)
    for route in ROUTES:
        ans = route(conn, user, q, as_of)
        if ans and ans.hits and ans.sentences:
            return ans
    return None
