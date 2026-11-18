"""ระดับ 1: ขยายแหล่งตามเส้นความสัมพันธ์ระหว่างกฎหมาย (graph-augmented retrieval แบบเบา)

หลังค้นแบบ hybrid ได้ผลอันดับต้นแล้ว ดึงรายการที่ "เชื่อม" กับผลเหล่านั้นมาเพิ่ม โดยใช้ความสัมพันธ์ที่บันทึกไว้
ในฐานข้อมูลเดิม (JOIN ธรรมดา ไม่ต้องมี graph DB):

  มาตราใน พ.ร.บ.        → กฎหมายลำดับรองที่ออกตามมาตรานั้น (ทะเบียน) ที่ชื่อตรงกับคำถาม
  ข้อของระเบียบ/ประกาศ  → มาตราแม่บทใน พ.ร.บ. (บทอาศัยอำนาจ)
  ระเบียนในทะเบียน      → ข้อในตัวบทเต็มของฉบับนั้นที่ตรงคำถามที่สุด (ถ้าโหลดตัวบทแล้ว)

เดินได้สองทอด (มาตรา → ทะเบียน → ข้อในตัวบท) เส้นทุกเส้นผ่าน authz และกรองวันที่มีผล ณ วันที่ถามเหมือนการค้นปกติ
แหล่งที่ได้จากการขยายมีคะแนนต่ำกว่าแหล่งต้นทางเล็กน้อย จึงไม่แย่งอันดับแรก และติด h.via บอกที่มา
"""
import re
from datetime import date

from sqlalchemy.engine import Connection

from .. import authz
from ..auth import User
from ..db import rows
from ..law import relations
from .hybrid import Hit
from .thai import content_terms, tokenize

PARENT_ACT = "eli/th/act/2550/energy-industry"
SEC_RE = re.compile(rf"^{re.escape(PARENT_ACT)}/sec-(\d+)$")
SEEDS = 6          # ขยายจากผลอันดับต้นกี่รายการ
MAX_ADD = 4        # เพิ่มได้รวมไม่เกิน
DECAY = 0.9
MIN_TITLE_OVERLAP = 0.34

_COLS = "chunk_id, kind, ref_id, title, chunk_text, eli_id, version_no, valid_from, valid_to, classification"


def _overlap(q_terms: set[str], text: str) -> float:
    return len(q_terms & set(tokenize(text))) / (len(q_terms) or 1)


def _to_hit(r: dict, parent: Hit, via: str) -> Hit:
    h = Hit(**{k: r[k] for k in _COLS.replace(" ", "").split(",")})
    h.score = parent.score * DECAY
    h.coverage, h.cosine = parent.coverage, parent.cosine
    h.via = via
    return h


def _subordinate(conn, user, sec: int, q_terms, limit=2) -> list[dict]:
    perm, params = authz.search_filter_sql(user)
    cands = rows(conn, f"""SELECT {_COLS} FROM search_chunk
                           WHERE kind = 'register' AND {perm}
                             AND ref_id IN (SELECT dst_register_id FROM law_relation
                                            WHERE rel_type = 'authorizes' AND src_eli = :s)""",
                 s=f"{PARENT_ACT}/sec-{sec}", **params)
    scored = sorted(((_overlap(q_terms, c["title"]), c) for c in cands), key=lambda x: -x[0])
    return [c for s, c in scored[:limit] if s >= MIN_TITLE_OVERLAP]


def _parents(conn, user, instrument_eli: str, as_of: date, limit=2) -> list[dict]:
    srcs = relations.authority_sections(conn, instrument_eli=instrument_eli)
    if not srcs:
        return []
    perm, params = authz.search_filter_sql(user)
    return rows(conn, f"""SELECT {_COLS} FROM search_chunk
                          WHERE kind = 'provision' AND eli_id = ANY(:e) AND {perm}
                            AND valid_from <= :d AND (valid_to IS NULL OR :d <= valid_to) LIMIT :n""",
                e=srcs, d=as_of, n=limit, **params)


def _best_provision(conn, user, instrument_eli: str, as_of: date, q_terms) -> dict | None:
    perm, params = authz.search_filter_sql(user)
    cands = rows(conn, f"""SELECT {_COLS} FROM search_chunk
                           WHERE kind = 'provision' AND eli_id LIKE :p AND {perm}
                             AND valid_from <= :d AND (valid_to IS NULL OR :d <= valid_to)""",
                 p=instrument_eli + "/%", d=as_of, **params)
    scored = sorted(((_overlap(q_terms, c["chunk_text"]), c) for c in cands), key=lambda x: -x[0])
    return scored[0][1] if scored and scored[0][0] >= MIN_TITLE_OVERLAP else None


def _instrument_of(eli: str | None) -> str | None:
    if not eli:
        return None
    return eli.rsplit("/", 1)[0] if re.search(r"/(sec|clause|item)-[\w-]+$", eli) else None


def expand(conn: Connection, user: User, hits: list[Hit], question: str, as_of: date) -> list[Hit]:
    q_terms = content_terms(question)
    have = {(h.kind, h.ref_id): h for h in hits}  # บทบัญญัติยาวมีหลายท่อน (chunk) — นับเป็นรายการเดียว
    added: list[Hit] = []

    def add(r: dict, parent: Hit, via: str) -> Hit | None:
        if len(added) >= MAX_ADD:
            return None
        key = (r["kind"], r["ref_id"])
        if key in have:
            h = have[key]
            if h.score < parent.score * DECAY:      # มีอยู่แล้วแต่อันดับต่ำ → ดันขึ้นมาใกล้ต้นทาง
                h.score, h.via = parent.score * DECAY, via
            return None
        h = _to_hit(r, parent, via)
        have[key] = h
        added.append(h)
        return h

    for seed in sorted(hits, key=lambda h: -h.score)[:SEEDS]:
        if seed.kind == "provision":
            m = SEC_RE.match(seed.eli_id or "")
            if m:
                for r in _subordinate(conn, user, int(m.group(1)), q_terms):
                    reg = add(r, seed, f"ออกตามมาตรา {m.group(1)}")
                    if reg and reg.eli_id:                                   # ทอดที่สอง: เข้าไปในตัวบทเต็ม
                        p = _best_provision(conn, user, reg.eli_id, as_of, q_terms)
                        if p:
                            add(p, reg, "ตัวบทของฉบับที่ออกตามมาตรานี้")
            else:
                inst = _instrument_of(seed.eli_id)
                if inst:
                    for r in _parents(conn, user, inst, as_of):
                        add(r, seed, "บทอาศัยอำนาจของฉบับนี้")
        elif seed.kind == "register" and seed.eli_id:
            p = _best_provision(conn, user, seed.eli_id, as_of, q_terms)
            if p:
                add(p, seed, "ตัวบทเต็มของระเบียนนี้")
            for r in _parents(conn, user, seed.eli_id, as_of, limit=1):
                add(r, seed, "บทอาศัยอำนาจของฉบับนี้")
    return hits + added
