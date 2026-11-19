"""หน้าตั้งค่าผู้ดูแลระบบ (ADM): LLM, reranker, embedding (endpoint / model / API key) และ embedding index"""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..db import get_conn, get_engine
from ..search import vector_index
from . import llm_config, model_config, store

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class LlmUpdate(BaseModel):
    provider: str | None = None
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=500)   # ไม่ส่ง = คงเดิม, "" = ลบคีย์ที่ตั้งจากหน้าเว็บ
    timeout_s: float | None = None


@router.get("/llm")
def get_llm(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    return llm_config.public_view(conn)


@router.put("/llm")
def put_llm(body: LlmUpdate, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    before = llm_config.effective(conn)
    try:
        changed = llm_config.save(conn, user.user_id, body.model_dump(exclude_none=True))
    except llm_config.ConfigError as e:
        raise HTTPException(400, str(e)) from e
    after = llm_config.effective(conn)
    audit.record(conn, user, "admin.llm.update", "settings/llm", detail={
        "changed": changed,  # ไม่บันทึกค่า API key
        "provider": [before.provider, after.provider], "base_url": [before.base_url, after.base_url],
        "model": [before.model, after.model], "external": after.external})
    return llm_config.public_view(conn)


@router.delete("/llm")
def reset_llm(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """กลับไปใช้ค่าจาก .env ทั้งหมด"""
    authz.require_admin(user)
    n = llm_config.reset(conn)
    audit.record(conn, user, "admin.llm.reset", "settings/llm", detail={"removed": n})
    return llm_config.public_view(conn)


@router.post("/llm/test")
def test_llm(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ทดสอบเชื่อมต่อด้วยข้อความสั้น ๆ (ไม่มีข้อมูลในระบบ) — คืนเวลาตอบและข้อความตัวอย่าง"""
    authz.require_admin(user)
    from ..assistant.llm import LlmUnavailable, stream_chat
    cfg = llm_config.effective(conn)
    if cfg.provider == "extractive":
        return {"ok": True, "latency_ms": 0, "sample": "โหมด extractive ไม่เรียกโมเดล", "provider": cfg.provider}
    t0 = time.monotonic()
    try:
        out = ""
        for d in stream_chat("ตอบสั้นที่สุด", "ตอบคำว่า OK เท่านั้น", cfg):
            out += d
            if len(out) > 200:
                break
        ok, err = True, None
    except LlmUnavailable as e:
        ok, out, err = False, "", str(e)[:300]
    ms = int((time.monotonic() - t0) * 1000)
    audit.record(conn, user, "admin.llm.test", "settings/llm", outcome="ALLOW" if ok else "ERROR",
                 detail={"provider": cfg.provider, "base_url": cfg.base_url, "model": cfg.model, "latency_ms": ms})
    return {"ok": ok, "latency_ms": ms, "sample": out.strip()[:200], "error": err, "provider": cfg.provider,
            "model": cfg.model, "external": cfg.external}


# ---------------------------------------------------------------- reranker / embedding
class ModelUpdate(BaseModel):
    provider: str | None = None
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=500)
    timeout_s: float | None = None
    candidates: int | None = None        # rerank เท่านั้น
    model_weight: float | None = None    # rerank เท่านั้น


def _model_view(name: str, conn: Connection) -> dict:
    v = model_config.public_view(name, conn)
    if name == "embed":
        idx = vector_index.list_all(conn)
        v["indexes"] = idx
        v["active_index"] = next((i["index_id"] for i in idx if i["active"]), None)
        v["pgvector"] = vector_index.has_pgvector(conn)
    return v


def _put_model(name: str, body: ModelUpdate, user: User, conn: Connection) -> dict:
    authz.require_admin(user)
    group = model_config.GROUPS[name]
    changes = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in group.fields}
    try:
        changed = store.save(conn, user.user_id, group, changes)
    except store.ConfigError as e:
        raise HTTPException(400, str(e)) from e
    view = model_config.public_view(name, conn)
    audit.record(conn, user, f"admin.{name}.update", f"settings/{name}", detail={
        "changed": changed, "provider": view["provider"], "base_url": view["base_url"], "model": view["model"],
        "external": view["external"]})
    return _model_view(name, conn)


def _reset_model(name: str, user: User, conn: Connection) -> dict:
    authz.require_admin(user)
    n = store.reset(conn, model_config.GROUPS[name])
    audit.record(conn, user, f"admin.{name}.reset", f"settings/{name}", detail={"removed": n})
    return _model_view(name, conn)


@router.get("/rerank")
def get_rerank(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    return _model_view("rerank", conn)


@router.put("/rerank")
def put_rerank(body: ModelUpdate, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    return _put_model("rerank", body, user, conn)


@router.delete("/rerank")
def reset_rerank(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    return _reset_model("rerank", user, conn)


# ข้อความทดสอบเป็นข้อความสมมติ (ไม่ใช่ข้อมูลในระบบ) จึงส่งไปบริการภายนอกได้
_TEST_Q = "ยื่นอุทธรณ์ภายในกี่วัน"
_TEST_DOCS = ["ผู้รับใบอนุญาตมีสิทธิอุทธรณ์ภายในสามสิบวันนับแต่วันที่ได้รับแจ้งคำสั่ง", "ค่าธรรมเนียมใบอนุญาตชำระเป็นรายปี"]


@router.post("/rerank/test")
def test_rerank(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    from ..search.rerank import _model_scores
    cfg = model_config.rerank_effective(conn)
    if cfg.provider in ("none", "legal"):
        return {"ok": True, "latency_ms": 0, "provider": cfg.provider,
                "sample": "ไม่เรียกโมเดล" if cfg.provider == "none" else "ใช้คุณลักษณะทางกฎหมายในระบบ (ไม่เรียกโมเดล)"}
    t0 = time.monotonic()
    sc = _model_scores(_TEST_Q, _TEST_DOCS, cfg)
    ms = int((time.monotonic() - t0) * 1000)
    ok = sc is not None and len(sc) == 2
    audit.record(conn, user, "admin.rerank.test", "settings/rerank", outcome="ALLOW" if ok else "ERROR",
                 detail={"provider": cfg.provider, "base_url": cfg.base_url, "latency_ms": ms})
    return {"ok": ok, "latency_ms": ms, "provider": cfg.provider, "model": cfg.model, "external": cfg.external,
            "sample": (f"คะแนนข้อความที่ตรง {sc[0]:.3f} · ไม่ตรง {sc[1]:.3f}" + ("" if sc[0] > sc[1] else " — ลำดับผิด ตรวจโมเดล"))
            if ok else "", "error": None if ok else "เรียกโมเดลไม่ได้ หรือรูปแบบคำตอบไม่ถูกต้อง (ดู log ของ api)"}


@router.get("/embed")
def get_embed(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    return _model_view("embed", conn)


@router.put("/embed")
def put_embed(body: ModelUpdate, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    return _put_model("embed", body, user, conn)


@router.delete("/embed")
def reset_embed(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    return _reset_model("embed", user, conn)


@router.post("/embed/test")
def test_embed(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    from ..search.embeddings import EmbedUnavailable, model_embed
    cfg = model_config.embed_effective(conn)
    if cfg.provider == "hash":
        return {"ok": True, "latency_ms": 0, "provider": "hash", "sample": "hash ในเครื่อง (ไม่เรียกโมเดล)"}
    t0 = time.monotonic()
    try:
        a, b, c = model_embed(cfg, [_TEST_Q, _TEST_DOCS[0], _TEST_DOCS[1]])
        dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
        ok, err = True, None
        sample = f"มิติ {len(a)} · cosine ข้อความที่ตรง {dot(a, b):.3f} · ไม่ตรง {dot(a, c):.3f}"
    except EmbedUnavailable as e:
        ok, err, sample = False, str(e), ""
    ms = int((time.monotonic() - t0) * 1000)
    audit.record(conn, user, "admin.embed.test", "settings/embed", outcome="ALLOW" if ok else "ERROR",
                 detail={"provider": cfg.provider, "base_url": cfg.base_url, "model": cfg.model, "latency_ms": ms})
    return {"ok": ok, "latency_ms": ms, "provider": cfg.provider, "model": cfg.model, "external": cfg.external,
            "sample": sample, "error": err}


class IndexCreate(BaseModel):
    activate_when_ready: bool = True


@router.post("/embed/indexes")
def create_index(body: IndexCreate, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """สร้าง index ใหม่จากค่าตั้งที่บันทึกแล้ว แล้วเริ่มสร้างเวกเตอร์เบื้องหลัง (index เดิมใช้ต่อจนกว่าอันใหม่พร้อม)"""
    authz.require_admin(user)
    cfg = model_config.embed_effective(conn)
    try:
        with get_engine().begin() as c2:  # commit ทันที ให้งานเบื้องหลังเห็นแถวใหม่
            iid = vector_index.create(c2, cfg, user.user_id, body.activate_when_ready)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit.record(conn, user, "admin.embed.index_create", f"embedding_index/{iid}", detail={
        "provider": cfg.provider, "base_url": cfg.base_url, "model": cfg.model, "external": cfg.external})
    vector_index.build_in_background(iid)
    return _model_view("embed", conn)


@router.post("/embed/indexes/{index_id}/update")
def update_index(index_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """สร้างเวกเตอร์เฉพาะเนื้อหาที่ยังไม่มี (หลัง seed หรือทำต่อหลังล้มเหลว)"""
    authz.require_admin(user)
    if vector_index.get(conn, index_id) is None:
        raise HTTPException(404, "ไม่พบ index")
    audit.record(conn, user, "admin.embed.index_update", f"embedding_index/{index_id}")
    vector_index.build_in_background(index_id)
    return _model_view("embed", conn)


@router.post("/embed/indexes/{index_id}/activate")
def activate_index(index_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    try:
        vector_index.activate(conn, index_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit.record(conn, user, "admin.embed.index_activate", f"embedding_index/{index_id}")
    return _model_view("embed", conn)


@router.post("/embed/deactivate")
def deactivate_index(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """กลับไปใช้ hash embedding ตั้งต้น"""
    authz.require_admin(user)
    vector_index.deactivate(conn)
    audit.record(conn, user, "admin.embed.index_deactivate", "embedding_index")
    return _model_view("embed", conn)


@router.delete("/embed/indexes/{index_id}")
def delete_index(index_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    try:
        vector_index.delete(conn, index_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    audit.record(conn, user, "admin.embed.index_delete", f"embedding_index/{index_id}")
    return _model_view("embed", conn)
