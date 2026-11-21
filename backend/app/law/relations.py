"""ความสัมพันธ์ระหว่างกฎหมายแบบรวมศูนย์ (ตาราง law_relation)

ทุกเส้นเก็บทิศ src → dst เดียวกัน:

  authorizes  มาตราแม่บท → กฎหมายลำดับรอง (ทะเบียน/ตราสาร)          ที่มา: ทะเบียนกฎหมายลำดับรอง   status approved
  amends      ฉบับ/ข้อที่แก้ไข → มาตรา/ข้อที่ถูกแก้ไขหรือเพิ่ม          ที่มา: relations ในไฟล์ตัวบท     status approved
  repeals     ฉบับ/ข้อที่ยกเลิก → มาตรา/ข้อที่ถูกยกเลิก                 ที่มา: relations ในไฟล์ตัวบท     status approved
  refers_to   มาตรา/ข้อ → มาตรา/ข้ออื่นในฉบับเดียวกันที่อ้างถึง         ที่มา: สกัดจากถ้อยคำอัตโนมัติ     status auto
  remedy      ขั้นตอน/ช่องทางเยียวยาต่อเนื่อง (ระดับ 3)                ที่มา: ไฟล์ที่นิติกรอนุมัติ         status approved

rebuild() สร้างตารางใหม่ทั้งหมดจากแหล่งข้างต้น จึงรันซ้ำได้ผลเท่าเดิม (idempotent)
เส้นที่นิติกรบันทึกเองเก็บเป็นไฟล์ seed/relations/*.json (ไม่ใช่ *.draft.json) เพื่อให้ตรวจย้อนได้ใน git
"""
import json
import re
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import one, rows
from ..search.thai import normalize

PARENT_ACT = "eli/th/act/2550/energy-industry"
RELATIONS_DIR = Path(__file__).resolve().parents[1] / "seed" / "relations"

TYPE_TH = {"authorizes": "ออกโดยอาศัยอำนาจตาม", "amends": "แก้ไข/เพิ่มเติม", "repeals": "ยกเลิก",
           "refers_to": "อ้างถึง", "remedy": "ช่องทางเยียวยาต่อเนื่อง"}
# ประเภทเส้นที่เชื่อถือได้พอให้เดินหลายทอดตอบคำถาม (auto = สกัดอัตโนมัติ ใช้ช่วยค้นแต่ไม่ใช้เป็นข้อสรุป)
TRUSTED = ("approved",)

_REF = re.compile(r"(มาตรา|ข้อ)\s*(\d+(?:/\d+)?)((?:\s*(?:,|และ|หรือ)\s*(?:มาตรา|ข้อ)?\s*\d+(?:/\d+)?)*)")
_MORE = re.compile(r"\d+(?:/\d+)?")
# "มาตรา 5 แห่งพระราชบัญญัติ…" / "ตามกฎหมายว่าด้วย…" = อ้างกฎหมายฉบับอื่น ไม่ผูกเป็นมาตราในฉบับนี้
_OTHER_LAW = re.compile(r"^\s*(?:วรรค\S*\s*)?(?:\(\d+\)\s*)?(?:แห่ง|ของ)(?!\s*(?:พระราชบัญญัติ|ระเบียบ|ประกาศ)นี้)")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", normalize(s or "")).strip()


class _Index:
    """ตารางค้นตราสาร/มาตราในระบบ สำหรับผูกข้อความอ้างอิงเป็น ELI"""

    def __init__(self, conn: Connection):
        self.instruments = [(_clean(r["title_th"]), r["eli_id"], r["instrument_id"])
                            for r in rows(conn, "SELECT instrument_id, eli_id, title_th FROM legal_instrument")]
        self.instruments.sort(key=lambda x: -len(x[0]))
        self.prov = {(r["instrument_id"], r["provision_no"]): r["eli_id"]
                     for r in rows(conn, "SELECT instrument_id, provision_no, eli_id FROM provision")}
        self.inst_id = {eli: iid for _, eli, iid in self.instruments}

    def provision(self, instrument_eli: str, kind: str, no: str) -> str | None:
        return self.prov.get((self.inst_id.get(instrument_eli), f"{kind} {no}"))

    def resolve(self, ref_text: str) -> str | None:
        """'ระเบียบ … (ฉบับที่ 3) พ.ศ. 2560 ข้อ 4' → ELI ของข้อ 4 (หรือของตราสาร ถ้าไม่ระบุข้อ)"""
        t = _clean(ref_text)
        for title, eli, _ in self.instruments:
            if t.startswith(title) or title in t:
                rest = t[t.find(title) + len(title):]
                m = re.match(r"\s*(มาตรา|ข้อ)\s*(\d+(?:/\d+)?)", rest)
                return (self.provision(eli, m.group(1), m.group(2)) if m else None) or eli
        return None


PROVISION_SUFFIX = re.compile(r"/(sec|clause|item)-[\w-]+$")   # ELI ของบทบัญญัติ = ELI ตราสาร + ส่วนท้ายนี้


def instrument_of(eli: str) -> str:
    """ELI ของตราสารที่บทบัญญัตินี้อยู่ (ELI ตราสารคืนค่าเดิม)"""
    return PROVISION_SUFFIX.sub("", eli)


def _from_register(conn: Connection) -> int:
    return conn.execute(text(f"""
        INSERT INTO law_relation (rel_type, src_eli, dst_register_id, dst_eli, dst_text, valid_from,
                                  status, source, source_ref)
        SELECT 'authorizes', '{PARENT_ACT}/sec-' || s, r.register_id, r.instrument_eli, r.title_th, r.effective_date,
               'approved', 'register', 'ทะเบียน #' || r.reg_no || ' (แถว ' || r.sheet_row || ')'
        FROM law_register r, unnest(r.authority_secs) s
        WHERE r.status <> 'GROUP'""")).rowcount


_EDITION = re.compile(r"\(?\s*(?:แก้ไขเพิ่มเติม\s*)?ฉบับที่\s*([2-9]|\d{2})\s*\)?")
PENDING_NOTE = "ฉบับแก้ไขตามทะเบียน — ยังไม่ได้ประกอบเข้าตัวบทฉบับหลัก"


def _stem(title: str) -> str:
    """ชื่อเรื่องไม่รวม (ฉบับที่ N) และ พ.ศ. — ใช้จับคู่ฉบับแก้ไขกับฉบับหลัก"""
    t = _clean(title).replace("คณะกรรมการกำกับกิจการพลังงาน", "กกพ.")
    t = _EDITION.sub(" ", t)
    t = re.sub(r"พ\s*\.\s*ศ\s*\.\s*\d{4}", " ", t)
    return re.sub(r"[\s.]", "", t)


def _amendment_families(conn: Connection) -> int:
    """ฉบับแก้ไข (ฉบับที่ 2, 3, …) → ฉบับหลักชื่อเดียวกัน ระดับตราสาร จากทะเบียน (เฉพาะฉบับที่มีตัวบทในระบบทั้งคู่)"""
    regs = rows(conn, """SELECT DISTINCT ON (instrument_eli) instrument_eli, title_th, effective_date, reg_no
                         FROM law_register WHERE instrument_eli IS NOT NULL AND status <> 'GROUP'
                         ORDER BY instrument_eli, sheet_row""")
    base: dict[str, dict] = {}
    for r in regs:
        if not _EDITION.search(_clean(r["title_th"])):
            base.setdefault(_stem(r["title_th"]), r)
    n = 0
    for r in regs:
        m = _EDITION.search(_clean(r["title_th"]))
        b = base.get(_stem(r["title_th"])) if m else None
        if not b or b["instrument_eli"] == r["instrument_eli"]:
            continue
        done = one(conn, """SELECT 1 AS x FROM law_relation WHERE source = 'law_json'
                            AND src_eli LIKE :s AND dst_eli LIKE :d LIMIT 1""",
                   s=r["instrument_eli"] + "/%", d=b["instrument_eli"] + "/%")
        conn.execute(text("""INSERT INTO law_relation (rel_type, src_eli, dst_eli, valid_from, status, source, source_ref, note)
                             VALUES ('amends', :s, :d, :vf, 'auto', 'register', :ref, :note)"""),
                     {"s": r["instrument_eli"], "d": b["instrument_eli"], "vf": r["effective_date"],
                      "ref": f"ทะเบียน #{r['reg_no']} (ฉบับที่ {m.group(1)})",
                      "note": "ประกอบเข้าตัวบทแล้ว" if done else PENDING_NOTE})
        n += 1
    return n


def pending_amendments(conn: Connection, instrument_eli: str) -> list[dict]:
    """ฉบับแก้ไขของตราสารนี้ที่ยังไม่ได้ประกอบเข้าตัวบท — ถ้อยคำที่แสดงอาจไม่เป็นปัจจุบัน"""
    return rows(conn, """
        SELECT r.src_eli AS eli, i.title_th AS title, r.valid_from,
               (SELECT p.eli_id FROM provision p WHERE p.instrument_id = i.instrument_id ORDER BY p.sort_order LIMIT 1)
                 AS first_provision_eli
        FROM law_relation r JOIN legal_instrument i ON i.eli_id = r.src_eli
        WHERE r.rel_type = 'amends' AND r.dst_eli = :e AND r.source = 'register' AND r.note = :n
        ORDER BY r.valid_from""", e=instrument_eli, n=PENDING_NOTE)


def amends_target(conn: Connection, instrument_eli: str) -> dict | None:
    """ตราสารนี้เป็นฉบับแก้ไขของฉบับใด"""
    return one(conn, """
        SELECT r.dst_eli AS eli, i.title_th AS title, r.note,
               (SELECT p.eli_id FROM provision p WHERE p.instrument_id = i.instrument_id ORDER BY p.sort_order LIMIT 1)
                 AS first_provision_eli
        FROM law_relation r JOIN legal_instrument i ON i.eli_id = r.dst_eli
        WHERE r.rel_type = 'amends' AND r.src_eli = :e AND r.source = 'register' LIMIT 1""", e=instrument_eli)


def _from_law_json(conn: Connection, idx: _Index) -> int:
    """legal_relation (ข้อความตามตัวบท) → เส้นมาตรฐาน; ประเภทที่ลงท้าย 'โดย' เป็นรูปถูกกระทำ (อีกฝ่ายเป็นต้นทาง)"""
    n = 0
    for r in rows(conn, """SELECT lr.relation_type, lr.target_text, pv.version_id, pv.valid_from, pv.valid_to, p.eli_id
                           FROM legal_relation lr JOIN provision_version pv USING (version_id)
                           JOIN provision p USING (provision_id)"""):
        rtype = "repeals" if "ยกเลิก" in r["relation_type"] else "amends"
        other = idx.resolve(r["target_text"])
        passive = r["relation_type"].strip().endswith("โดย")
        src, src_text, dst, dst_text = ((other, r["target_text"], r["eli_id"], None) if passive
                                        else (r["eli_id"], None, other, r["target_text"]))
        conn.execute(text("""INSERT INTO law_relation (rel_type, src_eli, src_text, dst_eli, dst_text, valid_from, valid_to,
                                                       status, source, source_ref, note)
                             VALUES (:t, :s, :st, :d, :dt, :vf, :vt, 'approved', 'law_json', :ref, :note)"""),
                     {"t": rtype, "s": src, "st": src_text, "d": dst, "dt": dst_text, "vf": r["valid_from"],
                      "vt": r["valid_to"] if rtype == "repeals" else None,
                      "ref": f"version_id {r['version_id']}", "note": r["relation_type"]})
        n += 1
    return n


_PARENT_NAME = re.compile(r"^\s*(?:วรรค\S*\s*)?(?:\(\d+\)\s*)?แห่งพระราชบัญญัติการประกอบกิจการพลังงาน")


def extract_refs(body: str, own_no: str, *, erc_subordinate: bool = False) -> list[tuple[str, str, bool]]:
    """มาตรา/ข้อที่ถ้อยคำอ้างถึง: [('มาตรา', '50', to_parent_act), …] — ไม่รวมตัวเองและการอ้างกฎหมายฉบับอื่น

    erc_subordinate: ข้อความในระเบียบ/ประกาศ กกพ. — "มาตรา N" หมายถึงมาตราของ พ.ร.บ.การประกอบกิจการพลังงาน
    """
    out: list[tuple[str, str, bool]] = []
    t = normalize(body)
    head = re.match(r"\s*(มาตรา|ข้อ)\s*\d+(?:/\d+)?", t)  # เลขมาตราของตัวเองที่ขึ้นต้นข้อความ
    for m in _REF.finditer(t, head.end() if head else 0):
        tail = t[m.end(): m.end() + 60]
        to_parent = bool(_PARENT_NAME.match(tail)) or (erc_subordinate and m.group(1) == "มาตรา")
        if not to_parent and (_OTHER_LAW.match(tail) or
                              re.match(r"\s*(?:แห่ง)?\s*(?:ประมวล|พระราชกฤษฎีกา|กฎกระทรวง)", tail)):
            continue
        for no in [m.group(2), *_MORE.findall(m.group(3) or "")]:
            ref = (m.group(1), no, to_parent)
            if (to_parent or f"{ref[0]} {ref[1]}" != own_no) and ref not in out:
                out.append(ref)
    return out


def _from_text(conn: Connection, idx: _Index) -> int:
    n = 0
    for r in rows(conn, """SELECT p.eli_id, p.provision_no, i.eli_id AS inst, pv.version_id, pv.body_text,
                                  pv.valid_from, pv.valid_to
                           FROM provision_version pv JOIN provision p USING (provision_id)
                           JOIN legal_instrument i USING (instrument_id)
                           WHERE pv.status = 'approved' AND i.eli_id NOT LIKE '%-amend-%'"""):
        # ฉบับแก้ไข (…-amend-N) อ้าง "ข้อ 21" ของฉบับหลัก ไม่ใช่ของตัวเอง — ข้ามไว้ก่อน (ได้จาก amends แทน)
        erc = r["inst"].startswith(("eli/th/reg/erc/", "eli/th/ann/erc/"))
        for kind, no, to_parent in extract_refs(r["body_text"], r["provision_no"], erc_subordinate=erc):
            dst = idx.provision(PARENT_ACT if to_parent else r["inst"], kind, no)
            if not dst:
                continue
            conn.execute(text("""INSERT INTO law_relation (rel_type, src_eli, dst_eli, valid_from, valid_to, status,
                                                           source, source_ref)
                                 VALUES ('refers_to', :s, :d, :vf, :vt, 'auto', 'text_ref', :ref)"""),
                         {"s": r["eli_id"], "d": dst, "vf": r["valid_from"], "vt": r["valid_to"],
                          "ref": f"version_id {r['version_id']}"})
            n += 1
    return n


def _from_files(conn: Connection) -> int:
    """seed/relations/*.json ที่นิติกรอนุมัติแล้ว (ไฟล์ *.draft.json ไม่โหลด)

    รูปแบบ: {"status": "approved", "approved_by": "...", "approved_at": "YYYY-MM-DD",
             "edges": [{"id": "R01", "type": "remedy", "from": ELI, "to": ELI, "note": "..."}]}
    """
    n = 0
    for f in sorted(RELATIONS_DIR.glob("*.json")) if RELATIONS_DIR.is_dir() else []:
        if f.name.endswith(".draft.json"):
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("status") != "approved":
            continue
        for e in doc.get("edges", []):
            conn.execute(text("""INSERT INTO law_relation (rel_type, src_eli, dst_eli, status, source, source_ref, note,
                                                           approved_by, approved_at)
                                 VALUES (:t, :s, :d, 'approved', 'manual', :ref, :note, :by, :at)"""),
                         {"t": e.get("type", "remedy"), "s": e["from"], "d": e["to"], "ref": f"{f.name}#{e.get('id', '')}",
                          "note": e.get("note") or e.get("condition"), "by": doc.get("approved_by"),
                          "at": doc.get("approved_at")})
            n += 1
    return n


def rebuild(conn: Connection) -> dict[str, int]:
    """สร้าง law_relation ใหม่จากทุกแหล่ง — เรียกหลังนำเข้าตัวบทหรือทะเบียนทุกครั้ง"""
    conn.execute(text("DELETE FROM law_relation"))
    idx = _Index(conn)
    out = {"authorizes": _from_register(conn), "law_json": _from_law_json(conn, idx),
           "refers_to": _from_text(conn, idx), "manual": _from_files(conn)}
    out["families"] = _amendment_families(conn)  # หลัง law_json เพื่อรู้ว่าฉบับไหนประกอบตัวบทแล้ว
    return out


# ---------------------------------------------------------------- queries
def section_eli(sec: int) -> str:
    return f"{PARENT_ACT}/sec-{sec}"


def authority_sections(conn: Connection, *, register_id: int | None = None,
                       instrument_eli: str | None = None) -> list[str]:
    """มาตราแม่บท (ELI) ของกฎหมายลำดับรอง ระบุด้วย register_id หรือ ELI ของตราสาร"""
    found = rows(conn, """
        SELECT DISTINCT src_eli FROM law_relation
        WHERE rel_type = 'authorizes'
          AND ((CAST(:r AS int) IS NOT NULL AND dst_register_id = :r) OR (CAST(:e AS text) IS NOT NULL AND dst_eli = :e))""",
                 r=register_id, e=instrument_eli)
    return sorted((r["src_eli"] for r in found), key=lambda e: int(re.sub(r"\D", "", e.rsplit("-", 1)[-1]) or 0))


def subordinate_counts(conn: Connection) -> dict[str, int]:
    return {r["src_eli"]: r["n"] for r in rows(conn, """
        SELECT src_eli, count(DISTINCT dst_register_id) AS n FROM law_relation
        WHERE rel_type = 'authorizes' AND dst_register_id IS NOT NULL GROUP BY src_eli""")}


def edges_of(conn: Connection, eli: str) -> dict[str, list[dict]]:
    """เส้นเข้า/ออกของมาตรา/ข้อ หรือตราสาร (ไม่รวม authorizes ที่มีแผงของตัวเองอยู่แล้ว)"""
    cols = "rel_type, src_eli, src_text, dst_eli, dst_text, valid_from, valid_to, status, source, note"
    out = rows(conn, f"""SELECT {cols} FROM law_relation
                         WHERE src_eli = :e AND rel_type <> 'authorizes' ORDER BY rel_type, dst_eli""", e=eli)
    inn = rows(conn, f"""SELECT {cols} FROM law_relation
                         WHERE dst_eli = :e AND rel_type <> 'authorizes' ORDER BY rel_type, src_eli""", e=eli)
    labels = {r["eli_id"]: r["label"] for r in rows(conn, """
        SELECT p.eli_id, p.provision_no || ' ' || coalesce(i.short_title, i.title_th) AS label
        FROM provision p JOIN legal_instrument i USING (instrument_id) WHERE p.eli_id = ANY(:e)
        UNION ALL SELECT eli_id, title_th FROM legal_instrument WHERE eli_id = ANY(:e)""",
        e=list({x["src_eli"] for x in inn} | {x["dst_eli"] for x in out} - {None}))}
    def dedupe(items: list[dict], end: str, txt: str) -> list[dict]:
        """เส้นเดียวกันที่บันทึกไว้หลายรุ่น → แสดงครั้งเดียว (เก็บรุ่นล่าสุด)"""
        best: dict[tuple, dict] = {}
        for x in items:
            x["label"] = labels.get(x[end]) or _clean(x[txt] or "")
            k = (x["rel_type"], x[end] or x["label"])
            if k not in best or (x["valid_from"] or date.min) > (best[k]["valid_from"] or date.min):
                best[k] = x
        return list(best.values())

    return {"outgoing": dedupe(out, "dst_eli", "dst_text"), "incoming": dedupe(inn, "src_eli", "src_text")}


def walk(conn: Connection, start_eli: str, types: tuple[str, ...] = ("remedy",), max_depth: int = 4,
         statuses: tuple[str, ...] = TRUSTED) -> list[dict]:
    """เดินตามเส้นหลายทอด (ระดับ 3) ด้วย recursive CTE — กันวนซ้ำด้วย path"""
    return rows(conn, """
        WITH RECURSIVE w(depth, src_eli, dst_eli, rel_type, note, path) AS (
            SELECT 1, src_eli, dst_eli, rel_type, note, ARRAY[src_eli, dst_eli]::text[]
            FROM law_relation
            WHERE src_eli = :s AND rel_type = ANY(:t) AND status = ANY(:st) AND dst_eli IS NOT NULL
          UNION ALL
            SELECT w.depth + 1, r.src_eli, r.dst_eli, r.rel_type, r.note, w.path || r.dst_eli::text
            FROM w JOIN law_relation r ON r.src_eli = w.dst_eli
            WHERE w.depth < :d AND r.rel_type = ANY(:t) AND r.status = ANY(:st)
              AND r.dst_eli IS NOT NULL AND NOT r.dst_eli = ANY(w.path))
        SELECT depth, src_eli, dst_eli, rel_type, note, path FROM w ORDER BY depth, dst_eli""",
                s=start_eli, t=list(types), d=max_depth, st=list(statuses))


def stats(conn: Connection) -> list[dict]:
    return rows(conn, """SELECT rel_type, status, source, count(*) AS n,
                                count(*) FILTER (WHERE src_eli IS NULL OR (dst_eli IS NULL AND dst_register_id IS NULL)) AS unresolved
                         FROM law_relation GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""")

