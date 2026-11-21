"""Embedding index จากโมเดล (bge-m3 ฯลฯ) — เก็บแยกจาก search_chunk และสลับใช้ได้โดยไม่ต้อง seed ใหม่

- แต่ละ index ผูกกับ provider/endpoint/model หนึ่งชุด; เวกเตอร์ผูกกับ content_sha (เนื้อหา) จึงใช้ต่อได้แม้ search_chunk
  ถูกสร้างใหม่ตอน seed — ต้อง "อัปเดต" เฉพาะเนื้อหาใหม่
- มี index active ได้ครั้งละหนึ่ง; ไม่มี index active → ใช้ hash ใน search_chunk.embedding (พฤติกรรมเดิม)
- โมเดลภายนอก (external): สร้างเวกเตอร์เฉพาะตัวบทกฎหมายสาธารณะ ข้อมูลภายในไม่ถูกส่งออก และไม่อยู่ในรายการ dense
  (ยังค้นเจอด้วยคำ/ความครอบคลุมคำตามเดิม)
- มี pgvector → สร้าง HNSW แบบ partial ต่อ index และค้นใน SQL; ไม่มี → คำนวณ cosine ใน numpy เฉพาะ chunk ที่มีสิทธิ์
"""
import logging
import threading

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_engine, rows
from .embeddings import EmbedUnavailable, model_embed

log = logging.getLogger(__name__)

SHAREABLE_SQL = "kind = 'provision' AND classification = 'public' AND NOT is_demo"
BATCH = 64
TOP_K = 50
ANN_MAX_DIM = 2000  # ขีดจำกัด HNSW ของชนิด vector
_running: set[int] = set()
_lock = threading.Lock()


def has_pgvector(conn: Connection) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar())


def get(conn: Connection, index_id: int) -> dict | None:
    r = rows(conn, "SELECT * FROM embedding_index WHERE index_id = :i", i=index_id)
    return r[0] if r else None


def active_index(conn: Connection) -> dict | None:
    try:
        with conn.begin_nested():  # savepoint: ตารางยังไม่มี (ยังไม่ได้ seed schema ใหม่) ต้องไม่ทำให้ transaction ค้นหาล้ม
            r = rows(conn, "SELECT * FROM embedding_index WHERE active")
    except Exception:
        return None
    return r[0] if r else None


def query_config(idx: dict):
    """ค่าตั้งสำหรับฝังคำค้นด้วยโมเดลเดียวกับ index — ใช้ API key ปัจจุบันเฉพาะเมื่อ endpoint เดียวกัน"""
    from ..admin import model_config
    cur = model_config.embed_effective()
    key = cur.api_key if cur.base_url == idx["base_url"] else ""
    return model_config.EmbedConfig(idx["provider"], idx["base_url"], idx["model"], key, cur.timeout_s,
                                    idx["external"], {})


# ---------------------------------------------------------------- build
def _pending_sql(idx: dict, count: bool = False) -> str:
    share = f"AND {SHAREABLE_SQL}" if idx["external"] else ""
    body = f"""FROM search_chunk s WHERE content_sha IS NOT NULL {share}
               AND NOT EXISTS (SELECT 1 FROM embedding_vec v WHERE v.index_id = :i AND v.content_sha = s.content_sha)"""
    if count:
        return f"SELECT count(DISTINCT content_sha) {body}"
    return f"SELECT DISTINCT ON (content_sha) content_sha, title || E'\\n' || chunk_text AS t {body} LIMIT {BATCH}"


def coverage(conn: Connection, idx: dict) -> dict:
    """จำนวนเนื้อหา (ไม่ซ้ำ) ที่มี/ยังไม่มีเวกเตอร์ และที่ถูกงดส่งเพราะเป็นข้อมูลภายใน"""
    total = conn.execute(text("SELECT count(DISTINCT content_sha) FROM search_chunk")).scalar() or 0
    shareable = conn.execute(text(f"SELECT count(DISTINCT content_sha) FROM search_chunk WHERE {SHAREABLE_SQL}")).scalar() or 0
    have = conn.execute(text("""SELECT count(DISTINCT s.content_sha) FROM search_chunk s
                                JOIN embedding_vec v ON v.content_sha = s.content_sha AND v.index_id = :i"""),
                        {"i": idx["index_id"]}).scalar() or 0
    eligible = shareable if idx["external"] else total
    return {"contents": total, "eligible": eligible, "embedded": have, "missing": max(eligible - have, 0),
            "withheld": total - shareable if idx["external"] else 0}


def create(conn: Connection, cfg, user_id: str, activate_when_ready: bool = True) -> int:
    if cfg.provider == "hash":
        raise ValueError("provider = hash ใช้เวกเตอร์ใน search_chunk อยู่แล้ว ไม่ต้องสร้าง index")
    return conn.execute(text("""
        INSERT INTO embedding_index (provider, base_url, model, external, created_by, activate_when_ready)
        VALUES (:p, :u, :m, :x, :by, :a) RETURNING index_id"""),
        {"p": cfg.provider, "u": cfg.base_url, "m": cfg.model, "x": cfg.external, "by": user_id,
         "a": activate_when_ready}).scalar()


def build(index_id: int, cfg=None) -> dict:
    """สร้างเวกเตอร์ของเนื้อหาที่ยังไม่มีใน index (ทำซ้ำได้: ใช้ทั้งสร้างครั้งแรก ทำต่อหลังขัดจังหวะ และอัปเดตหลัง seed)"""
    with _lock:
        if index_id in _running:
            return {"status": "building", "note": "กำลังสร้างอยู่แล้ว"}
        _running.add(index_id)
    eng = get_engine()
    try:
        with eng.begin() as conn:
            idx = get(conn, index_id)
            if idx is None:
                return {"status": "missing"}
            cfg = cfg or query_config(idx)
            cov = coverage(conn, idx)
            conn.execute(text("""UPDATE embedding_index SET status = 'building', error = NULL, total = :t, done = :d,
                                 withheld = :w, finished_at = NULL WHERE index_id = :i"""),
                         {"t": cov["eligible"], "d": cov["embedded"], "w": cov["withheld"], "i": index_id})
        while True:
            with eng.begin() as conn:
                batch = rows(conn, _pending_sql(idx), i=index_id)
                if not batch:
                    break
                vecs = model_embed(cfg, [b["t"] for b in batch])
                dim = len(vecs[0])
                if idx["dim"] and dim != idx["dim"]:
                    raise EmbedUnavailable(f"มิติเวกเตอร์เปลี่ยนจาก {idx['dim']} เป็น {dim} — สร้าง index ใหม่")
                for b, v in zip(batch, vecs):
                    conn.execute(text("""INSERT INTO embedding_vec (index_id, content_sha, vec) VALUES (:i, :s, :v)
                                         ON CONFLICT DO NOTHING"""), {"i": index_id, "s": b["content_sha"], "v": v})
                conn.execute(text("UPDATE embedding_index SET done = done + :n, dim = :d WHERE index_id = :i"),
                             {"n": len(batch), "d": dim, "i": index_id})
                idx["dim"] = dim
        with eng.begin() as conn:
            conn.execute(text("UPDATE embedding_index SET status = 'ready', finished_at = now() WHERE index_id = :i"),
                         {"i": index_id})
            idx = get(conn, index_id)
            ensure_ann(conn, idx)
            if idx["activate_when_ready"] and not idx["active"]:
                activate(conn, index_id)
            return {"status": "ready", **coverage(conn, idx)}
    except Exception as e:
        log.warning("embedding index %s failed: %s", index_id, e)
        with eng.begin() as conn:
            conn.execute(text("UPDATE embedding_index SET status = 'failed', error = :e, finished_at = now() WHERE index_id = :i"),
                         {"e": str(e)[:500], "i": index_id})
        return {"status": "failed", "error": str(e)[:500]}
    finally:
        with _lock:
            _running.discard(index_id)


def build_in_background(index_id: int) -> None:
    threading.Thread(target=build, args=(index_id,), daemon=True, name=f"embed-index-{index_id}").start()


def mark_interrupted(conn: Connection) -> None:
    """ตอน API เริ่ม: งานที่ค้างสถานะ building จาก process เดิมถือว่าถูกขัดจังหวะ (กดอัปเดตเพื่อทำต่อ)"""
    conn.execute(text("""UPDATE embedding_index SET status = 'failed', error = 'ถูกขัดจังหวะ (API restart) — กดอัปเดตเพื่อทำต่อ'
                         WHERE status = 'building'"""))


def ensure_ann(conn: Connection, idx: dict) -> bool:
    if not idx.get("dim") or idx["dim"] > ANN_MAX_DIM or not has_pgvector(conn):
        return False
    i, d = int(idx["index_id"]), int(idx["dim"])
    conn.execute(text(f"""CREATE INDEX IF NOT EXISTS embedding_vec_hnsw_{i} ON embedding_vec
                          USING hnsw ((vec::vector({d})) vector_cosine_ops) WHERE index_id = {i}"""))
    return True


def activate(conn: Connection, index_id: int) -> None:
    idx = get(conn, index_id)
    if idx is None or idx["status"] != "ready":
        raise ValueError("ใช้ได้เฉพาะ index ที่สร้างเสร็จแล้ว")
    conn.execute(text("UPDATE embedding_index SET active = FALSE WHERE active AND index_id <> :i"), {"i": index_id})
    conn.execute(text("UPDATE embedding_index SET active = TRUE WHERE index_id = :i"), {"i": index_id})


def deactivate(conn: Connection) -> None:
    conn.execute(text("UPDATE embedding_index SET active = FALSE, activate_when_ready = FALSE WHERE active"))


def delete(conn: Connection, index_id: int) -> None:
    with _lock:
        if index_id in _running:
            raise ValueError("กำลังสร้างอยู่ — รอให้เสร็จก่อน")
    conn.execute(text(f"DROP INDEX IF EXISTS embedding_vec_hnsw_{int(index_id)}"))
    conn.execute(text("DELETE FROM embedding_index WHERE index_id = :i"), {"i": index_id})


def list_all(conn: Connection) -> list[dict]:
    out = []
    for idx in rows(conn, "SELECT * FROM embedding_index ORDER BY index_id DESC"):
        idx["coverage"] = coverage(conn, idx)
        idx["running"] = idx["index_id"] in _running
        idx["ann"] = bool(conn.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
                                       {"n": f"embedding_vec_hnsw_{idx['index_id']}"}).scalar())
        out.append(idx)
    return out


# ---------------------------------------------------------------- search
def dense(conn: Connection, q: str, cand: list[dict], where: str, params: dict) -> list[tuple[dict, float]]:
    """รายการ dense สำหรับ RRF: index ที่ active (ถ้ามีและเรียกโมเดลได้) มิฉะนั้น hash ใน search_chunk"""
    if not cand:
        return []
    idx = active_index(conn)
    if idx is not None:
        try:
            qv = model_embed(query_config(idx), [q])[0]
            return _dense_model(conn, idx, qv, cand, where, params)
        except EmbedUnavailable as e:
            log.warning("embedding model unavailable, falling back to hash: %s", e)
    from .embeddings import hash_embed
    qv = np.asarray(hash_embed([q])[0], dtype=np.float32)
    mat = np.asarray([c["embedding"] for c in cand], dtype=np.float32)
    sims = mat @ qv
    return [(cand[i], float(sims[i])) for i in np.argsort(-sims)[:TOP_K]]


def _dense_model(conn: Connection, idx: dict, qv: list[float], cand: list[dict], where: str,
                 params: dict) -> list[tuple[dict, float]]:
    by_id = {c["chunk_id"]: c for c in cand}
    d = len(qv)
    if idx.get("dim") == d and d <= ANN_MAX_DIM and has_pgvector(conn):
        lit = "[" + ",".join(f"{x:.7g}" for x in qv) + "]"
        res = conn.execute(text(f"""
            SELECT s.chunk_id, 1 - (v.vec::vector({d}) <=> CAST(:qv AS vector({d}))) AS sim
            FROM embedding_vec v JOIN search_chunk s ON s.content_sha = v.content_sha
            WHERE v.index_id = :iid AND {where}
            ORDER BY v.vec::vector({d}) <=> CAST(:qv AS vector({d})) LIMIT {TOP_K}"""),
            {**params, "qv": lit, "iid": idx["index_id"]}).all()
        return [(by_id[r[0]], float(r[1])) for r in res if r[0] in by_id]
    shas = list({c["content_sha"] for c in cand if c.get("content_sha")})
    vec = {r[0]: r[1] for r in conn.execute(text(
        "SELECT content_sha, vec FROM embedding_vec WHERE index_id = :i AND content_sha = ANY(:s)"),
        {"i": idx["index_id"], "s": shas}).all()}
    have = [c for c in cand if c.get("content_sha") in vec]
    if not have:
        return []
    mat = np.asarray([vec[c["content_sha"]] for c in have], dtype=np.float32)
    sims = mat @ np.asarray(qv, dtype=np.float32)
    return [(have[i], float(sims[i])) for i in np.argsort(-sims)[:TOP_K]]


def update_active_cli() -> None:
    """python -m app.search.vector_index — อัปเดต index ที่ active หลัง seed (เฉพาะเนื้อหาใหม่)"""
    with get_engine().begin() as conn:
        idx = active_index(conn)
    if idx is None:
        print("ไม่มี embedding index ที่ active — ใช้ hash ใน search_chunk")
        return
    print(build(idx["index_id"]))


if __name__ == "__main__":
    update_active_cli()
