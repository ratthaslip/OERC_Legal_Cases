"""สร้าง search_chunk จากข้อมูลหลัก (index เป็น read model สร้างใหม่ได้เสมอ — ไม่ใช่แหล่งความจริง)

ระบบจริงจะเรียก reindex_* จาก event (law.version_approved, appeal.updated, case.access_changed)
ใน POC เรียกหลัง seed และหลังแก้ข้อมูลที่กระทบ index
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import rows
from .embeddings import embed
from .thai import to_document


def _insert(conn: Connection, chunks: list[dict]) -> None:
    if not chunks:
        return
    vecs = embed([c["title"] + "\n" + c["chunk_text"] for c in chunks])
    for c, v in zip(chunks, vecs):
        conn.execute(text("""
            INSERT INTO search_chunk (kind, ref_id, eli_id, version_no, valid_from, valid_to, title, chunk_text,
                                      tokens, embedding, classification, owners, energy_sector, is_demo, weight)
            VALUES (:kind, :ref_id, :eli_id, :version_no, :valid_from, :valid_to, :title, :chunk_text,
                    to_tsvector('simple', :doc), :emb, :classification, :owners, :energy_sector, :is_demo, :weight)"""),
            {"eli_id": None, "version_no": None, "valid_from": None, "valid_to": None, "energy_sector": None,
             "owners": [], "is_demo": False, "weight": 1.0, **c, "doc": to_document(c["title"] + " " + c["chunk_text"]), "emb": v})


def reindex_laws(conn: Connection) -> int:
    conn.execute(text("DELETE FROM search_chunk WHERE kind = 'provision'"))
    data = rows(conn, """
        SELECT pv.version_id, pv.version_no, pv.body_text, pv.summary, pv.valid_from, pv.valid_to,
               p.eli_id, p.provision_no, i.title_th, i.short_title, i.category
        FROM provision_version pv JOIN provision p USING (provision_id) JOIN legal_instrument i USING (instrument_id)
        WHERE pv.status = 'approved'""")
    chunks = []
    for d in data:
        base = {"kind": "provision", "ref_id": d["version_id"], "eli_id": d["eli_id"], "version_no": d["version_no"],
                "valid_from": d["valid_from"], "valid_to": d["valid_to"], "classification": "public",
                "title": f"{d['provision_no']} {d['title_th']} (รุ่นที่ {d['version_no']})",
                "weight": source_weight(d["eli_id"], d["provision_no"], d["category"])}
        for part in split_long(d["body_text"]):
            chunks.append({**base, "chunk_text": f"{d['provision_no']} {part}\nสาระสำคัญ: {d['summary'] or ''}"})
    _insert(conn, chunks)
    return len(chunks)


# น้ำหนักแหล่ง (คูณคะแนนค้นหา): พ.ร.บ. มีศักดิ์สูงกว่าและเป็นคำตอบหลักของคำถามทั่วไป; ระเบียบบริหารภายในสำนักงาน
# (โครงสร้าง/บุคลากร/การเงิน) และเอกสารแนบท้ายยาว ๆ มีคำกว้างจนติดผลค้นหาทุกเรื่อง จึงลดน้ำหนักลง
WEIGHT_ACT, WEIGHT_ADMIN, WEIGHT_ANNEX, WEIGHT_WHOLE = 1.15, 0.8, 0.85, 0.9
WEIGHT_REGISTER_WITH_TEXT = 0.85


def source_weight(eli: str, provision_no: str, category: str | None) -> float:
    w = WEIGHT_ACT if "/act/" in eli else 1.0
    if category == "การบริหารองค์กร":
        w *= WEIGHT_ADMIN
    if provision_no == "แนบท้าย":
        w *= WEIGHT_ANNEX
    elif provision_no == "ทั้งฉบับ":
        w *= WEIGHT_WHOLE
    return round(w, 3)


CHUNK_CHARS = 900  # ข้อความยาว (บัญชีแนบท้าย, ประกาศที่ไม่แบ่งข้อ) แบ่งเป็นท่อน — กันเอกสารยาวชนะเพราะมีทุกคำ


def split_long(body: str, size: int = CHUNK_CHARS) -> list[str]:
    """แบ่งตามย่อหน้าให้แต่ละท่อนยาวไม่เกิน ~size อักษร ท่อนติดกันซ้อน 1 ย่อหน้า (ผลค้นหารวมเป็นบทบัญญัติเดียว)"""
    if len(body) <= size * 1.5:
        return [body]
    paras = [p for p in body.split("\n") if p.strip()]
    out, cur = [], []
    for p in paras:
        while len(p) > size * 1.5:          # ย่อหน้าเดียวยาวมาก (ตาราง) → ตัดตามความยาว
            if cur:
                out.append("\n".join(cur)); cur = []
            out.append(p[:size]); p = p[size:]
        if cur and sum(len(x) for x in cur) + len(p) > size:
            out.append("\n".join(cur))
            cur = [cur[-1]] if len(cur[-1]) < size // 2 else []
        cur.append(p)
    if cur:
        out.append("\n".join(cur))
    return out


def reindex_register(conn: Connection) -> int:
    """ทะเบียนกฎหมายลำดับรอง: 1 ระเบียน = 1 chunk (สาธารณะ — เฉพาะชื่อ ประเภท บทอาศัยอำนาจ วันใช้บังคับ สถานะ)"""
    from ..law.register import chunk_text

    conn.execute(text("DELETE FROM search_chunk WHERE kind = 'register'"))
    chunks = []
    for r in rows(conn, "SELECT * FROM law_register ORDER BY register_id"):
        title, body = chunk_text(r)
        chunks.append({"kind": "register", "ref_id": r["register_id"], "eli_id": r["instrument_eli"],
                       "valid_from": r["effective_date"], "classification": "public", "title": title,
                       "chunk_text": body,
                       # มีตัวบทเต็มแล้ว → ตัวบทเป็นหลักฐานที่ดีกว่าแถวทะเบียน (แถวทะเบียนไว้สำหรับฉบับที่ยังไม่มีตัวบท)
                       "weight": WEIGHT_REGISTER_WITH_TEXT if r["instrument_eli"] else 1.0})
    _insert(conn, chunks)
    return len(chunks)


def _case_chunks(kind: str, ref_id: int, title: str, body: str, docs: list[dict], cls: str, owners: list[str],
                 sector: str, is_demo: bool = False) -> list[dict]:
    base = {"kind": kind, "ref_id": ref_id, "classification": cls, "owners": owners, "energy_sector": sector,
            "is_demo": bool(is_demo)}
    out = [{**base, "title": title, "chunk_text": body}]
    for d in docs:
        out.append({**base, "title": f"{title} · {d['doc_type']}", "chunk_text": d["excerpt"]})
    return out


def reindex_cases(conn: Connection, only: tuple[str, int] | None = None) -> int:
    """only=('appeal', id) หรือ ('court_case', id) เพื่อ index ใหม่เฉพาะเรื่องเดียว"""
    kind_only, id_only = only if only else (None, None)
    if only:
        conn.execute(text("DELETE FROM search_chunk WHERE kind = :k AND ref_id = :i"), {"k": kind_only, "i": id_only})
    else:
        conn.execute(text("DELETE FROM search_chunk WHERE kind IN ('appeal', 'court_case')"))
    chunks: list[dict] = []
    for a in rows(conn, """SELECT a.*, ARRAY(SELECT user_id FROM appeal_owner o WHERE o.appeal_id = a.appeal_id) AS owners
                           FROM appeal a WHERE (CAST(:k AS text) IS NULL OR (:k = 'appeal' AND a.appeal_id = :i))""",
                  k=kind_only, i=id_only):
        docs = rows(conn, "SELECT doc_type, excerpt FROM case_document WHERE owner_type='appeal' AND owner_id=:i",
                    i=a["appeal_id"])
        kind_th = "อุทธรณ์" if a["case_type"] == "APPEAL" else "เรื่องร้องเรียน"
        body = (f"{kind_th} เรื่อง {a['subject']}\nผู้อุทธรณ์/ผู้ร้องเรียน: {a['party_label']}\nสรุป: {a['summary']}\n"
                f"ผลคำวินิจฉัย: {a['decision_result'] or 'อยู่ระหว่างพิจารณา'}")
        chunks += _case_chunks("appeal", a["appeal_id"], f"{kind_th} {a['appeal_no']}", body, docs,
                               a["classification"], a["owners"], a["energy_sector"], a["is_demo"])
    for k in rows(conn, """SELECT k.*, ARRAY(SELECT user_id FROM court_case_owner o WHERE o.case_id = k.case_id) AS owners
                           FROM court_case k WHERE (CAST(:k AS text) IS NULL OR (:k = 'court_case' AND k.case_id = :i))""",
                  k=kind_only, i=id_only):
        docs = rows(conn, "SELECT doc_type, excerpt FROM case_document WHERE owner_type='court_case' AND owner_id=:i",
                    i=k["case_id"])
        no = f"คดีหมายเลขดำที่ {k['black_no']}" + (f" หมายเลขแดงที่ {k['red_no']}" if k["red_no"] else "")
        body = (f"เรื่อง {k['subject']}\n{k['court_level']}\nผู้ฟ้องคดี: {k['party_label']}\nสรุป: {k['summary']}\n"
                f"ผลคดี: {k['result'] or ''}")
        chunks += _case_chunks("court_case", k["case_id"], no, body, docs, k["classification"], k["owners"],
                               k["energy_sector"], k["is_demo"])
    _insert(conn, chunks)
    return len(chunks)
