"""ค่าตั้ง Reranker และ Embedding ที่ผู้ดูแลแก้จากหน้าเว็บ (ทับค่าจาก .env)

ทั้งสองแบบเป็นบริการภายนอกได้ → ต้องไม่ส่งข้อมูลภายใน (ทะเบียน ตราสารภายใน สำนวน) ออกไป:
  reranker  — ส่งเฉพาะผลค้นที่เป็นตัวบทสาธารณะให้โมเดล ที่เหลือคงคะแนนเดิม
  embedding — สร้างเวกเตอร์เฉพาะตัวบทสาธารณะ (ข้อมูลภายในค้นด้วยคำ/ความครอบคลุมคำ ตามเดิม)
ตรวจภายนอก/ภายในจาก URL แบบเดียวกับ LLM; บังคับได้ด้วย RERANK_EXTERNAL / EMBED_EXTERNAL ใน .env
"""
import os
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from ..config import get_settings, llm_is_external
from . import store
from .store import FieldSpec, Group

RERANK_PROVIDERS = ("none", "legal", "tei", "cohere")
EMBED_PROVIDERS = ("hash", "openai_compat")

RERANK = Group("rerank.", {
    "provider": FieldSpec("rerank_provider", store.v_choice(*RERANK_PROVIDERS)),
    "base_url": FieldSpec("rerank_base_url", store.v_url("RERANK_ALLOWED_BASE_URLS")),
    "model": FieldSpec("rerank_model", store.v_model),
    "api_key": FieldSpec("rerank_api_key", secret=True),
    "candidates": FieldSpec("rerank_candidates", store.v_number(5, 100, as_int=True)),
    "model_weight": FieldSpec("rerank_model_weight", store.v_number(0, 1)),
    "timeout_s": FieldSpec("rerank_timeout_s", store.v_number(1, 120)),
})
EMBED = Group("embed.", {
    "provider": FieldSpec("embed_provider", store.v_choice(*EMBED_PROVIDERS)),
    "base_url": FieldSpec("embed_base_url", store.v_url("EMBED_ALLOWED_BASE_URLS")),
    "model": FieldSpec("embed_model", store.v_model),
    "api_key": FieldSpec("embed_api_key", secret=True),
    "timeout_s": FieldSpec("embed_timeout_s", store.v_number(5, 600)),
})
GROUPS = {"rerank": RERANK, "embed": EMBED}


@dataclass
class RerankConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    candidates: int
    model_weight: float
    timeout_s: float
    external: bool
    source: dict


@dataclass
class EmbedConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_s: float
    external: bool
    source: dict


def _external(provider_uses_url: bool, url: str, env_var: str) -> bool:
    if not provider_uses_url:
        return False
    return llm_is_external("openai_compat", url, os.getenv(env_var, "auto"))


def rerank_effective(conn: Connection | None = None) -> RerankConfig:
    v, src, _ = store.values(RERANK, get_settings(), conn)
    uses_url = v["provider"] in ("tei", "cohere")
    return RerankConfig(v["provider"], v["base_url"].rstrip("/"), v["model"], v["api_key"], int(float(v["candidates"])),
                        float(v["model_weight"]), float(v["timeout_s"]),
                        _external(uses_url, v["base_url"], "RERANK_EXTERNAL"), src)


def embed_effective(conn: Connection | None = None) -> EmbedConfig:
    v, src, _ = store.values(EMBED, get_settings(), conn)
    return EmbedConfig(v["provider"], v["base_url"].rstrip("/"), v["model"], v["api_key"], float(v["timeout_s"]),
                       _external(v["provider"] == "openai_compat", v["base_url"], "EMBED_EXTERNAL"), src)


def public_view(name: str, conn: Connection | None = None) -> dict:
    group = GROUPS[name]
    cfg = rerank_effective(conn) if name == "rerank" else embed_effective(conn)
    _, _, last = store.values(group, get_settings(), conn)
    d = {k: v for k, v in cfg.__dict__.items() if k != "api_key"}
    d.update(has_key=bool(cfg.api_key), key_hint=f"…{cfg.api_key[-4:]}" if len(cfg.api_key) >= 8 else None,
             providers=list(RERANK_PROVIDERS if name == "rerank" else EMBED_PROVIDERS),
             allowed_base_urls=store.allowed_urls(f"{name.upper()}_ALLOWED_BASE_URLS"),
             secret_configured=store.secret_configured(),
             updated_by=last["updated_by"] if last else None, updated_at=last["updated_at"] if last else None)
    return d
