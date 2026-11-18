"""Hybrid search: BM25-like (Postgres FTS) + vector + Reciprocal Rank Fusion (SDD 6.3)

POC เก็บ embedding เป็น real[] และคำนวณ cosine ใน numpy เพราะข้อมูลมีหลักร้อย chunk
เมื่อข้อมูลจริงเกินหลักหมื่น ให้ย้ายไป pgvector (HNSW) หรือ OpenSearch โดยแก้เฉพาะไฟล์นี้
"""
from dataclasses import dataclass, field
from datetime import date

import math

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import authz
from ..auth import User
from .embeddings import embed
from .rerank import rerank
from .thai import content_terms, expand_query, normalize_ref_no, to_query

RRF_K = 60
OMIT_MIN_COVERAGE = 0.35


@dataclass
class Hit:
    chunk_id: int
    kind: str
    ref_id: int
    title: str
    chunk_text: str
    eli_id: str | None
    version_no: int | None
    valid_from: date | None
    valid_to: date | None
    classification: str
    score: float = 0.0
    lexical: bool = False
    cosine: float = 0.0
    coverage: float = 0.0   # สัดส่วนคำสำคัญของคำค้น (ถ่วงด้วย IDF) ที่พบใน chunk
    rrf: float = 0.0
    exact_ref: bool = False
    via: str | None = None  # มาจากการขยายตามเส้นความสัมพันธ์ (search/graph.py)
    is_demo: bool = False   # มาจากเรื่อง/คดีสาธิต
    weight: float = 1.0     # น้ำหนักแหล่ง (indexer.source_weight)


@dataclass
class SearchResult:
    hits: list[Hit] = field(default_factory=list)
    restricted_omitted: int = 0


def _base_where(user: User, kinds: list[str], as_of: date | None, sector: str | None,
                exclude_demo: bool = False) -> tuple[str, dict]:
    perm_sql, params = authz.search_filter_sql(user)
    where = [perm_sql, "kind = ANY(:kinds)"]
    if exclude_demo:
        where.append("NOT is_demo")
    params["kinds"] = kinds
    if as_of is not None:
        where.append("(kind <> 'provision' OR (valid_from <= :as_of AND (valid_to IS NULL OR :as_of <= valid_to)))")
        params["as_of"] = as_of
    if sector:
        where.append("(kind IN ('provision', 'register') OR energy_sector = :sector)")
        params["sector"] = sector
    return " AND ".join(where), params


def hybrid_search(conn: Connection, user: User, q: str, kinds: list[str], as_of: date | None = None,
                  limit: int = 20, sector: str | None = None, exclude_demo: bool = False) -> SearchResult:
    q = expand_query(q)
    tsq = to_query(q)
    where, params = _base_where(user, kinds, as_of, sector, exclude_demo)
    cols = ("chunk_id, kind, ref_id, title, chunk_text, eli_id, version_no, valid_from, valid_to, classification, is_demo, "
            "weight")

    # 1) lexical (กรองสิทธิ์ใน WHERE ก่อนจัดอันดับ)
    lexical: list[dict] = []
    if tsq:
        lexical = [dict(r) for r in conn.execute(text(f"""
            SELECT {cols}, ts_rank_cd(tokens, to_tsquery('simple', :tsq)) AS rank
            FROM search_chunk WHERE {where} AND tokens @@ to_tsquery('simple', :tsq)
            ORDER BY rank DESC LIMIT 50"""), {**params, "tsq": tsq}).mappings()]

    # 2) vector เฉพาะ chunk ที่ผู้ใช้มีสิทธิ์
    cand = [dict(r) for r in conn.execute(text(
        f"SELECT {cols}, embedding, ARRAY(SELECT lexeme FROM unnest(tokens)) AS lex FROM search_chunk WHERE {where}"),
        params).mappings()]
    # IDF จากชุดที่ผู้ใช้มีสิทธิ์ (คำที่พบทุกเอกสาร เช่น "อุทธรณ์" มีน้ำหนักน้อย)
    q_terms = content_terms(q)
    n_docs = max(len(cand), 1)
    df = {t: sum(1 for c in cand if t in c["lex"]) for t in q_terms}
    idf = {t: math.log(1 + n_docs / (1 + df[t])) for t in q_terms}
    idf_total = sum(idf.values()) or 1.0
    cov = {c["chunk_id"]: sum(idf[t] for t in q_terms if t in set(c["lex"])) / idf_total for c in cand}
    vector: list[tuple[dict, float]] = []
    if cand:
        qv = np.asarray(embed([q])[0], dtype=np.float32)
        mat = np.asarray([c["embedding"] for c in cand], dtype=np.float32)
        sims = mat @ qv
        order = np.argsort(-sims)[:50]
        vector = [(cand[i], float(sims[i])) for i in order]

    # 3) Reciprocal Rank Fusion
    fused: dict[int, Hit] = {}

    def hit_of(r: dict) -> Hit:
        if r["chunk_id"] not in fused:
            fused[r["chunk_id"]] = Hit(**{k: r[k] for k in Hit.__dataclass_fields__ if k in r})
        return fused[r["chunk_id"]]

    for rank, r in enumerate(lexical):
        h = hit_of(r)
        h.rrf += 1.0 / (RRF_K + rank + 1)
        h.lexical = True
    for rank, (r, sim) in enumerate(vector):
        h = hit_of(r)
        h.rrf += 1.0 / (RRF_K + rank + 1)
        h.cosine = sim
    # ผู้สมัครชุดที่ 3: ความครอบคลุมคำสำคัญถ่วง IDF (ไม่เอียงไปทางเอกสารยาวแบบ ts_rank และให้น้ำหนักคำหายาก เช่น "ทุเลา")
    #   จำเป็นเมื่อคลังใหญ่ขึ้น: คำทั่วไปจากคำย่อ (กกพ. → คณะกรรมการกำกับกิจการพลังงาน) ทำให้ ts_rank ดึงแต่เอกสารยาว
    by_cov = sorted((c for c in cand if cov.get(c["chunk_id"], 0) > 0), key=lambda c: -cov[c["chunk_id"]])[:50]
    for rank, r in enumerate(by_cov):
        h = hit_of(r)
        h.rrf += 1.0 / (RRF_K + rank + 1)
        h.lexical = True
    # คะแนนชุดแรก: ความครอบคลุมคำสำคัญ + อันดับ RRF
    max_rrf = max((h.rrf for h in fused.values()), default=1.0) or 1.0
    for h in fused.values():
        h.coverage = cov.get(h.chunk_id, 0.0)
        h.score = (0.65 * h.coverage + 0.35 * (h.rrf / max_rrf)) * h.weight

    # 3b) rerank ผู้สมัครอันดับต้น (search/rerank.py: legal features และ/หรือ cross-encoder)
    ref = normalize_ref_no(q)
    rerank(q, list(fused.values()), q_terms, idf,
           exact_ref_of=lambda h: bool(ref) and normalize_ref_no(h.title) == ref)

    # 4) ค้นด้วยเลขที่ (อธ. 104/2567, พ. 89/2565) → ดันขึ้นอันดับแรก
    if ref:
        for h in fused.values():
            if normalize_ref_no(h.title) == ref:
                h.exact_ref, h.score = True, h.score + 1.0

    # 5) รวมหลาย chunk ของเรื่องเดียวกัน เหลือ chunk ที่ดีที่สุด
    best: dict[tuple, Hit] = {}
    for h in sorted(fused.values(), key=lambda h: -h.score):
        key = (h.kind, h.ref_id)
        if key not in best:
            best[key] = h
    hits = sorted(best.values(), key=lambda h: -h.score)[:limit]

    # 6) นับเรื่องที่ตรงคำค้นแต่อยู่นอกสิทธิ์ (บอกแค่จำนวน ไม่บอกรายละเอียด)
    #    ใช้เกณฑ์ความครอบคลุมเดียวกับผลที่แสดง เพื่อไม่นับเรื่องที่ตรงแค่คำทั่วไป
    omitted = 0
    case_kinds = [k for k in kinds if k not in ("provision", "register")]
    if q_terms and case_kinds and user.role != "PUB":
        perm_sql, perm_params = authz.search_filter_sql(user)
        hidden = conn.execute(text(f"""
            SELECT kind, ref_id, title, ARRAY(SELECT lexeme FROM unnest(tokens)) AS lex FROM search_chunk
            WHERE kind = ANY(:ck) AND NOT {perm_sql} {"AND NOT is_demo" if exclude_demo else ""}"""),
            {**perm_params, "ck": case_kinds}).mappings()
        ref = normalize_ref_no(q)
        refs = set()
        for r in hidden:
            lex = set(r["lex"])
            c = sum(idf[t] for t in q_terms if t in lex) / idf_total
            if c >= OMIT_MIN_COVERAGE or (ref and normalize_ref_no(r["title"]) == ref):
                refs.add((r["kind"], r["ref_id"]))
        omitted = len(refs)
    return SearchResult(hits=hits, restricted_omitted=omitted)
