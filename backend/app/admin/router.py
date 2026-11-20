"""หน้าตั้งค่าผู้ดูแลระบบ (ADM): LLM, reranker, embedding (endpoint / model / API key) และ embedding index"""
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..db import get_conn, get_engine
from ..search import vector_index
from . import model_config, store

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
Name = Literal["llm", "rerank", "embed"]


class ModelUpdate(BaseModel):
    provider: str | None = None
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=500)   # ไม่ส่ง = คงเดิม, "" = ลบคีย์ที่ตั้งจากหน้าเว็บ
    timeout_s: float | None = None
    candidates: int | None = None        # rerank เท่านั้น
    model_weight: float | None = None    # rerank เท่านั้น


def _view(name: str, conn: Connection) -> dict:
    v = model_config.public_view(name, conn)
    if name == "embed":
        idx = vector_index.list_all(conn)
        v["indexes"] = idx
        v["active_index"] = next((i["index_id"] for i in idx if i["active"]), None)
        v["pgvector"] = vector_index.has_pgvector(conn)
    return v


@router.get("/{name}")
def get_settings(name: Name, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    return _view(name, conn)


@router.put("/{name}")
def put_settings(name: Name, body: ModelUpdate, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    group, effective, _, _ = model_config.SETTINGS[name]
    before = effective(conn)
    changes = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in group.fields}
    try:
        changed = store.save(conn, user.user_id, group, changes)
    except store.ConfigError as e:
        raise HTTPException(400, str(e)) from e
    after = effective(conn)
    audit.record(conn, user, f"admin.{name}.update", f"settings/{name}", detail={
        "changed": changed,  # ไม่บันทึกค่า API key
        **{k: [getattr(before, k), getattr(after, k)] for k in ("provider", "base_url", "model")},
        "external": after.external})
    return _view(name, conn)


@router.delete("/{name}")
def reset_settings(name: Name, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """กลับไปใช้ค่าจาก .env ทั้งหมด"""
    authz.require_admin(user)
    n = store.reset(conn, model_config.SETTINGS[name][0])
    audit.record(conn, user, f"admin.{name}.reset", f"settings/{name}", detail={"removed": n})
    return _view(name, conn)


# ---------------------------------------------------------------- ทดสอบการเชื่อมต่อ
# ข้อความทดสอบเป็นข้อความสมมติ (ไม่ใช่ข้อมูลในระบบ) จึงส่งไปบริการภายนอกได้
_TEST_Q = "ยื่นอุทธรณ์ภายในกี่วัน"
_TEST_DOCS = ["ผู้รับใบอนุญาตมีสิทธิอุทธรณ์ภายในสามสิบวันนับแต่วันที่ได้รับแจ้งคำสั่ง", "ค่าธรรมเนียมใบอนุญาตชำระเป็นรายปี"]
_NO_MODEL = {"llm": {"extractive": "โหมด extractive ไม่เรียกโมเดล"},
             "rerank": {"none": "ไม่เรียกโมเดล", "legal": "ใช้คุณลักษณะทางกฎหมายในระบบ (ไม่เรียกโมเดล)"},
             "embed": {"hash": "hash ในเครื่อง (ไม่เรียกโมเดล)"}}


def _probe_llm(cfg) -> str:
    from ..assistant.llm import LlmUnavailable, stream_chat
    out = ""
    try:
        for d in stream_chat("ตอบสั้นที่สุด", "ตอบคำว่า OK เท่านั้น", cfg):
            out += d
            if len(out) > 200:
                break
    except LlmUnavailable as e:
        raise RuntimeError(str(e)) from e
    return out.strip()[:200]


def _probe_rerank(cfg) -> str:
    from ..search.rerank import _model_scores
    sc = _model_scores(_TEST_Q, _TEST_DOCS, cfg)
    if sc is None or len(sc) != 2:
        raise RuntimeError("เรียกโมเดลไม่ได้ หรือรูปแบบคำตอบไม่ถูกต้อง (ดู log ของ api)")
    return f"คะแนนข้อความที่ตรง {sc[0]:.3f} · ไม่ตรง {sc[1]:.3f}" + ("" if sc[0] > sc[1] else " — ลำดับผิด ตรวจโมเดล")


def _probe_embed(cfg) -> str:
    from ..search.embeddings import EmbedUnavailable, model_embed
    try:
        a, b, c = model_embed(cfg, [_TEST_Q, *_TEST_DOCS])
    except EmbedUnavailable as e:
        raise RuntimeError(str(e)) from e
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
    return f"มิติ {len(a)} · cosine ข้อความที่ตรง {dot(a, b):.3f} · ไม่ตรง {dot(a, c):.3f}"


_PROBES = {"llm": _probe_llm, "rerank": _probe_rerank, "embed": _probe_embed}


@router.post("/{name}/test")
def test_settings(name: Name, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """ทดสอบเชื่อมต่อด้วยข้อความสมมติ (ไม่มีข้อมูลในระบบ) — คืนเวลาตอบและผลตัวอย่าง"""
    authz.require_admin(user)
    cfg = model_config.SETTINGS[name][1](conn)
    if cfg.provider in _NO_MODEL[name]:
        return {"ok": True, "latency_ms": 0, "provider": cfg.provider, "sample": _NO_MODEL[name][cfg.provider]}
    t0 = time.monotonic()
    try:
        sample, err = _PROBES[name](cfg), None
    except RuntimeError as e:
        sample, err = "", str(e)[:300]
    ms = int((time.monotonic() - t0) * 1000)
    audit.record(conn, user, f"admin.{name}.test", f"settings/{name}", outcome="ERROR" if err else "ALLOW",
                 detail={"provider": cfg.provider, "base_url": cfg.base_url, "model": cfg.model, "latency_ms": ms})
    return {"ok": err is None, "latency_ms": ms, "sample": sample, "error": err, "provider": cfg.provider,
            "model": cfg.model, "external": cfg.external}


# ---------------------------------------------------------------- embedding index
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
    return _view("embed", conn)


@router.post("/embed/indexes/{index_id}/update")
def update_index(index_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """สร้างเวกเตอร์เฉพาะเนื้อหาที่ยังไม่มี (หลัง seed หรือทำต่อหลังล้มเหลว)"""
    authz.require_admin(user)
    if vector_index.get(conn, index_id) is None:
        raise HTTPException(404, "ไม่พบ index")
    audit.record(conn, user, "admin.embed.index_update", f"embedding_index/{index_id}")
    vector_index.build_in_background(index_id)
    return _view("embed", conn)


@router.post("/embed/indexes/{index_id}/activate")
def activate_index(index_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    try:
        vector_index.activate(conn, index_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit.record(conn, user, "admin.embed.index_activate", f"embedding_index/{index_id}")
    return _view("embed", conn)


@router.post("/embed/deactivate")
def deactivate_index(user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    """กลับไปใช้ hash embedding ตั้งต้น"""
    authz.require_admin(user)
    vector_index.deactivate(conn)
    audit.record(conn, user, "admin.embed.index_deactivate", "embedding_index")
    return _view("embed", conn)


@router.delete("/embed/indexes/{index_id}")
def delete_index(index_id: int, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    authz.require_admin(user)
    try:
        vector_index.delete(conn, index_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    audit.record(conn, user, "admin.embed.index_delete", f"embedding_index/{index_id}")
    return _view("embed", conn)
