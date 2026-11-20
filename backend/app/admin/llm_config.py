"""ค่าตั้ง LLM ที่ผู้ดูแลแก้จากหน้าเว็บ — ทับค่าจาก .env (provider, endpoint, model, API key, timeout)

- เก็บผ่าน admin/store.py (ตาราง app_setting); API key เข้ารหัสด้วย CONFIG_SECRET และไม่ส่งกลับหน้าเว็บ
- LLM_ALLOWED_BASE_URLS (ไม่บังคับ): ถ้าตั้ง endpoint ต้องอยู่ในรายการ — เปิดใช้เมื่อระบบมีผู้ใช้หลายคน
- การป้องกัน LLM ภายนอก (ส่งเฉพาะตัวบทสาธารณะ) คำนวณจาก endpoint ที่ใช้จริงเสมอ; LLM_EXTERNAL ปรับได้เฉพาะใน .env
"""
import os
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from ..config import get_settings, llm_is_external
from . import store
from .store import FieldSpec, Group

PROVIDERS = ("extractive", "openai_compat", "anthropic")
GROUP = Group("llm.", {
    "provider": FieldSpec("llm_provider", store.v_choice(*PROVIDERS)),
    "base_url": FieldSpec("llm_base_url", store.v_url("LLM_ALLOWED_BASE_URLS")),
    "model": FieldSpec("llm_model", store.v_model),
    "api_key": FieldSpec("llm_api_key", secret=True),
    "timeout_s": FieldSpec("llm_timeout_s", store.v_number(5, 600)),
})


@dataclass
class LlmConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_s: float
    external: bool
    source: dict          # ค่าแต่ละช่องมาจาก env หรือ db


def effective(conn: Connection | None = None) -> LlmConfig:
    """ค่าที่ใช้จริง: ค่าจากหน้าเว็บ (ถ้ามี) ทับค่าจาก .env"""
    v, src, _ = store.values(GROUP, get_settings(), conn)
    ext = llm_is_external(v["provider"], v["base_url"], os.getenv("LLM_EXTERNAL", "auto"))
    return LlmConfig(v["provider"], v["base_url"].rstrip("/"), v["model"], v["api_key"], float(v["timeout_s"]), ext, src)


def public_view(conn: Connection | None = None) -> dict:
    """สิ่งที่หน้าเว็บเห็น — ไม่มี API key"""
    return store.public_view(GROUP, effective(conn), PROVIDERS, "LLM_ALLOWED_BASE_URLS", conn)


def invalidate() -> None:
    store.invalidate(GROUP.prefix)
