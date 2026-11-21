"""ที่เก็บค่าตั้งระบบที่ผู้ดูแลแก้จากหน้าเว็บ (ตาราง app_setting) — ใช้ร่วมกันทุกกลุ่ม: llm / rerank / embed

- ค่าในฐานข้อมูลทับค่าจาก .env เป็นรายช่อง; ค่าลับ (API key) เข้ารหัสด้วย CONFIG_SECRET (Fernet) และไม่ส่งกลับหน้าเว็บ
- อ่านแบบมี cache 10 วินาที (ค่าใหม่มีผลโดยไม่ต้อง restart); ส่ง conn มาเพื่ออ่านใน transaction ที่เพิ่งบันทึก
"""
import base64
import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..config import get_settings
from ..db import get_engine, rows

log = logging.getLogger(__name__)

_TTL_S = 10.0
_cache: dict[str, dict] = {}
_lock = threading.Lock()
_SQL = "SELECT key, value, secret, updated_by, updated_at FROM app_setting WHERE key LIKE :p"


class ConfigError(ValueError):
    pass


@dataclass
class FieldSpec:
    env_attr: str                                  # ชื่อ attribute ใน Settings (ค่าจาก .env)
    validate: Callable[[str], str] | None = None   # คืนค่าที่ทำความสะอาดแล้ว หรือ raise ConfigError
    secret: bool = False


@dataclass
class Group:
    prefix: str                                    # เช่น "llm."
    fields: dict[str, FieldSpec] = field(default_factory=dict)


def fernet():
    secret = os.getenv("CONFIG_SECRET", "")
    if not secret:
        return None
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def secret_configured() -> bool:
    return bool(os.getenv("CONFIG_SECRET"))


def load(prefix: str, conn: Connection | None = None) -> dict[str, dict]:
    if conn is not None:
        return {r["key"][len(prefix):]: r for r in rows(conn, _SQL, p=prefix + "%")}
    with _lock:
        c = _cache.get(prefix)
        if c and time.monotonic() - c["at"] < _TTL_S:
            return c["rows"]
    try:
        with get_engine().connect() as cn:
            data = {r["key"][len(prefix):]: r for r in rows(cn, _SQL, p=prefix + "%")}
    except Exception:
        data = {}
    with _lock:
        _cache[prefix] = {"at": time.monotonic(), "rows": data}
    return data


def invalidate(prefix: str | None = None) -> None:
    with _lock:
        if prefix is None:
            _cache.clear()
        else:
            _cache.pop(prefix, None)


def values(group: Group, settings, conn: Connection | None = None) -> tuple[dict[str, str], dict[str, str], dict | None]:
    """(ค่าที่ใช้จริง, แหล่งของแต่ละช่อง env|db, แถวที่แก้ล่าสุด)"""
    vals = {k: str(getattr(settings, f.env_attr)) for k, f in group.fields.items()}
    src = {k: "env" for k in vals}
    stored = load(group.prefix, conn)
    for k, r in stored.items():
        if k not in group.fields:
            continue
        v = r["value"]
        if r["secret"]:
            f = fernet()
            if not f:
                continue  # เปลี่ยน/ลบ CONFIG_SECRET → ถอดรหัสไม่ได้ → ใช้ค่าจาก .env (ไม่ล่ม)
            try:
                v = f.decrypt(v.encode()).decode()
            except Exception:  # ค่าที่ถอดรหัสไม่ได้ (เปลี่ยนกุญแจ) → ใช้ค่าจาก env แทน
                log.warning("cannot decrypt setting %s — falling back to environment", k)
                continue
        vals[k], src[k] = v, "db"
    last = max(stored.values(), key=lambda r: r["updated_at"], default=None)
    return vals, src, last


def save(conn: Connection, user_id: str, group: Group, changes: dict) -> list[str]:
    changed = []
    for k, v in changes.items():
        spec = group.fields.get(k)
        if spec is None or v is None:
            continue
        v = str(v).strip()
        secret = False
        if spec.secret:
            if v == "":  # ค่าว่าง = ลบค่าที่ตั้งจากหน้าเว็บ (กลับไปใช้ .env)
                conn.execute(text("DELETE FROM app_setting WHERE key = :k"), {"k": group.prefix + k})
                changed.append(k)
                continue
            f = fernet()
            if not f:
                raise ConfigError("ยังไม่ได้ตั้ง CONFIG_SECRET ใน .env — บันทึก API key จากหน้าเว็บไม่ได้")
            v, secret = f.encrypt(v.encode()).decode(), True
        elif spec.validate:
            v = spec.validate(v)
        conn.execute(text("""INSERT INTO app_setting (key, value, secret, updated_by, updated_at)
                             VALUES (:k, :v, :s, :u, now())
                             ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, secret = EXCLUDED.secret,
                                 updated_by = EXCLUDED.updated_by, updated_at = now()"""),
                     {"k": group.prefix + k, "v": v, "s": secret, "u": user_id})
        changed.append(k)
    invalidate(group.prefix)
    return changed


def reset(conn: Connection, group: Group) -> int:
    n = conn.execute(text("DELETE FROM app_setting WHERE key LIKE :p"), {"p": group.prefix + "%"}).rowcount
    invalidate(group.prefix)
    return n


def public_view(group: Group, cfg, providers, allow_env: str, conn: Connection | None = None) -> dict:
    """สิ่งที่หน้าเว็บเห็นของค่าตั้งกลุ่มหนึ่ง — ทุกช่องของ cfg ยกเว้น API key (แสดงเพียง 4 ตัวท้าย)"""
    _, _, last = values(group, get_settings(), conn)
    d = {k: v for k, v in cfg.__dict__.items() if k != "api_key"}
    d.update(has_key=bool(cfg.api_key), key_hint=f"…{cfg.api_key[-4:]}" if len(cfg.api_key) >= 8 else None,
             providers=list(providers), allowed_base_urls=allowed_urls(allow_env), secret_configured=secret_configured(),
             updated_by=last["updated_by"] if last else None, updated_at=last["updated_at"] if last else None)
    return d


# ---------------------------------------------------------------- validators
def allowed_urls(env_var: str) -> list[str]:
    raw = os.getenv(env_var, "").strip()
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def v_choice(*choices: str) -> Callable[[str], str]:
    def f(v: str) -> str:
        if v not in choices:
            raise ConfigError(f"ต้องเป็น {', '.join(choices)}")
        return v
    return f


def v_url(allow_env: str) -> Callable[[str], str]:
    def f(v: str) -> str:
        v = v.rstrip("/")
        if not re.match(r"^https?://[^\s/]+", v):
            raise ConfigError("endpoint ต้องขึ้นต้นด้วย http:// หรือ https://")
        allowed = allowed_urls(allow_env)
        if allowed and v not in allowed:
            raise ConfigError(f"endpoint นี้ไม่อยู่ในรายการที่อนุญาต ({allow_env})")
        return v
    return f


MODEL_RE = re.compile(r"^[\w.:/@+-]{1,120}$")


def v_model(v: str) -> str:
    if not MODEL_RE.match(v):
        raise ConfigError("ชื่อโมเดลมีอักขระที่ไม่อนุญาต")
    return v


def v_number(lo: float, hi: float, as_int: bool = False) -> Callable[[str], str]:
    def f(v: str) -> str:
        try:
            x = float(v)
        except ValueError as e:
            raise ConfigError("ต้องเป็นตัวเลข") from e
        if not lo <= x <= hi:
            raise ConfigError(f"ต้องอยู่ระหว่าง {lo:g}–{hi:g}")
        return str(int(x)) if as_int else str(x)
    return f
