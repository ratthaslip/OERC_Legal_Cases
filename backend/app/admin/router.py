"""หน้าตั้งค่าผู้ดูแลระบบ (ADM): LLM provider / endpoint / model / API key / timeout"""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from .. import audit, authz
from ..auth import User, current_user
from ..db import get_conn
from . import llm_config

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
