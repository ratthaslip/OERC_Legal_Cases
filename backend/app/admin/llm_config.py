"""ค่าตั้ง LLM ที่ผู้ดูแลแก้จากหน้าเว็บ — ทับค่าจาก .env (provider, endpoint, model, API key, timeout)

- เก็บในตาราง app_setting; API key เข้ารหัสด้วย CONFIG_SECRET (Fernet) และไม่ส่งกลับหน้าเว็บ (แสดงแค่ 4 ตัวท้าย)
- ไม่ตั้ง CONFIG_SECRET → บันทึก API key จากหน้าเว็บไม่ได้ (ใช้ LLM_API_KEY ใน .env ต่อไป)
- LLM_ALLOWED_BASE_URLS (ไม่บังคับ): ถ้าตั้ง endpoint ต้องอยู่ในรายการ — เปิดใช้เมื่อระบบมีผู้ใช้หลายคน
- การป้องกัน LLM ภายนอก (ส่งเฉพาะตัวบทสาธารณะ) คำนวณจาก endpoint ที่ใช้จริงเสมอ; LLM_EXTERNAL ปรับได้เฉพาะใน .env
"""
import base64
import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..config import get_settings, llm_is_external
from ..db import get_engine, rows

PROVIDERS = ("extractive", "openai_compat", "anthropic")
KEYS = ("provider", "base_url", "model", "api_key", "timeout_s")
_PREFIX = "llm."
_TTL_S = 10.0
_cache: dict = {"at": 0.0, "rows": {}}
_lock = threading.Lock()
MODEL_RE = re.compile(r"^[\w.:/@+-]{1,120}$")


@dataclass
class LlmConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_s: float
    external: bool
    source: dict          # ค่าแต่ละช่องมาจาก env หรือ db


def _fernet():
    secret = os.getenv("CONFIG_SECRET", "")
    if not secret:
        return None
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def secret_configured() -> bool:
    return bool(os.getenv("CONFIG_SECRET"))


_SQL = "SELECT key, value, secret, updated_by, updated_at FROM app_setting WHERE key LIKE 'llm.%'"


def _load_rows(conn: Connection | None = None) -> dict[str, dict]:
    """conn: อ่านใน transaction เดียวกับที่เพิ่งบันทึก (เห็นค่าที่ยังไม่ commit) — ไม่ใช้ cache"""
    if conn is not None:
        return {r["key"][len(_PREFIX):]: r for r in rows(conn, _SQL)}
    with _lock:
        if time.monotonic() - _cache["at"] < _TTL_S:
            return _cache["rows"]
    try:
        with get_engine().connect() as c:
            data = {r["key"][len(_PREFIX):]: r for r in rows(c, _SQL)}
    except Exception:  # noqa: BLE001 — ตารางยังไม่มี/ฐานข้อมูลไม่พร้อม → ใช้ค่าจาก .env
        data = {}
    with _lock:
        _cache.update(at=time.monotonic(), rows=data)
    return data


def invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0


def effective(conn: Connection | None = None) -> LlmConfig:
    """ค่าที่ใช้จริง: ค่าจากหน้าเว็บ (ถ้ามี) ทับค่าจาก .env"""
    s = get_settings()
    base = {"provider": s.llm_provider, "base_url": s.llm_base_url, "model": s.llm_model,
            "api_key": s.llm_api_key, "timeout_s": str(s.llm_timeout_s)}
    source = {k: "env" for k in base}
    for k, r in _load_rows(conn).items():
        if k not in base:
            continue
        val = r["value"]
        if r["secret"]:
            f = _fernet()
            if not f:
                continue  # เปลี่ยน/ลบ CONFIG_SECRET แล้ว → ถอดรหัสไม่ได้ ใช้ค่าจาก .env
            try:
                val = f.decrypt(val.encode()).decode()
            except Exception:  # noqa: BLE001
                continue
        base[k], source[k] = val, "db"
    ext = llm_is_external(base["provider"], base["base_url"], os.getenv("LLM_EXTERNAL", "auto"))
    return LlmConfig(base["provider"], base["base_url"].rstrip("/"), base["model"], base["api_key"],
                     float(base["timeout_s"]), ext, source)


def allowed_base_urls() -> list[str]:
    raw = os.getenv("LLM_ALLOWED_BASE_URLS", "").strip()
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def public_view(conn: Connection | None = None) -> dict:
    """สิ่งที่หน้าเว็บเห็น — ไม่มี API key"""
    cfg = effective(conn)
    meta = _load_rows(conn)
    last = max((r for r in meta.values()), key=lambda r: r["updated_at"], default=None)
    return {"provider": cfg.provider, "base_url": cfg.base_url, "model": cfg.model, "timeout_s": cfg.timeout_s,
            "has_key": bool(cfg.api_key), "key_hint": f"…{cfg.api_key[-4:]}" if len(cfg.api_key) >= 8 else None,
            "external": cfg.external, "source": cfg.source, "providers": list(PROVIDERS),
            "allowed_base_urls": allowed_base_urls(), "secret_configured": secret_configured(),
            "updated_by": last["updated_by"] if last else None, "updated_at": last["updated_at"] if last else None}


class ConfigError(ValueError):
    pass


def save(conn: Connection, user_id: str, changes: dict) -> list[str]:
    """บันทึกค่าที่เปลี่ยน — คืนรายชื่อช่องที่เปลี่ยน (ไม่คืนค่า API key)"""
    changed = []
    allowed = allowed_base_urls()
    for k, v in changes.items():
        if k not in KEYS or v is None:
            continue
        v = str(v).strip()
        secret = False
        if k == "provider" and v not in PROVIDERS:
            raise ConfigError(f"provider ต้องเป็น {', '.join(PROVIDERS)}")
        if k == "base_url":
            v = v.rstrip("/")
            if not re.match(r"^https?://[^\s/]+", v):
                raise ConfigError("endpoint ต้องขึ้นต้นด้วย http:// หรือ https://")
            if allowed and v not in allowed:
                raise ConfigError("endpoint นี้ไม่อยู่ในรายการที่อนุญาต (LLM_ALLOWED_BASE_URLS)")
        if k == "model" and not MODEL_RE.match(v):
            raise ConfigError("ชื่อโมเดลมีอักขระที่ไม่อนุญาต")
        if k == "timeout_s":
            try:
                t = float(v)
            except ValueError as e:
                raise ConfigError("timeout ต้องเป็นตัวเลข") from e
            if not 5 <= t <= 600:
                raise ConfigError("timeout ต้องอยู่ระหว่าง 5–600 วินาที")
        if k == "api_key":
            if v == "":  # ค่าว่าง = ลบคีย์ที่ตั้งจากหน้าเว็บ (กลับไปใช้ .env)
                conn.execute(text("DELETE FROM app_setting WHERE key = :k"), {"k": _PREFIX + k})
                changed.append(k)
                continue
            f = _fernet()
            if not f:
                raise ConfigError("ยังไม่ได้ตั้ง CONFIG_SECRET ใน .env — บันทึก API key จากหน้าเว็บไม่ได้")
            v, secret = f.encrypt(v.encode()).decode(), True
        conn.execute(text("""INSERT INTO app_setting (key, value, secret, updated_by, updated_at)
                             VALUES (:k, :v, :s, :u, now())
                             ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, secret = EXCLUDED.secret,
                                 updated_by = EXCLUDED.updated_by, updated_at = now()"""),
                     {"k": _PREFIX + k, "v": v, "s": secret, "u": user_id})
        changed.append(k)
    invalidate()
    return changed


def reset(conn: Connection) -> int:
    n = conn.execute(text("DELETE FROM app_setting WHERE key LIKE 'llm.%'")).rowcount
    invalidate()
    return n
