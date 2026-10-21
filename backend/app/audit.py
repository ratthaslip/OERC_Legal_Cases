"""Audit log แบบ append-only + hash chain (SDD 3.3 ops_db, NFR-AUD)

hash = SHA-256(prev_hash || เนื้อหา canonical JSON) ทำให้แก้หรือลบแถวกลางทางแล้วตรวจพบได้ด้วย verify_chain()
"""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .auth import User

GENESIS = "0" * 64


def _digest(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def record(conn: Connection, user: User, action: str, resource: str, outcome: str = "ALLOW",
           detail: dict | None = None) -> None:
    # advisory lock กันสองคำขอเขียนพร้อมกันแล้วได้ prev_hash ตัวเดียวกัน
    conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
    prev = conn.execute(text("SELECT hash FROM audit_event ORDER BY seq DESC LIMIT 1")).scalar() or GENESIS
    occurred_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "occurred_at": occurred_at, "actor_id": user.user_id, "actor_role": user.role,
        "action": action, "resource": resource, "outcome": outcome, "detail": detail or {},
    }
    conn.execute(
        text("""INSERT INTO audit_event (occurred_at, actor_id, actor_role, action, resource, outcome, detail,
                                         prev_hash, hash)
                VALUES (:occurred_at, :actor_id, :actor_role, :action, :resource, :outcome, CAST(:detail AS jsonb),
                        :prev, :hash)"""),
        {**payload, "detail": json.dumps(detail or {}, ensure_ascii=False), "prev": prev,
         "hash": _digest(prev, payload)},
    )


def verify_chain(conn: Connection) -> tuple[bool, int | None]:
    """คืน (ถูกต้องทั้งสาย?, seq แรกที่เสีย)"""
    prev = GENESIS
    for r in conn.execute(text("SELECT * FROM audit_event ORDER BY seq")).mappings():
        payload = {
            "occurred_at": r["occurred_at"].astimezone(timezone.utc).isoformat(), "actor_id": r["actor_id"], "actor_role": r["actor_role"],
            "action": r["action"], "resource": r["resource"], "outcome": r["outcome"], "detail": r["detail"],
        }
        if r["prev_hash"] != prev or r["hash"] != _digest(prev, payload):
            return False, r["seq"]
        prev = r["hash"]
    return True, None
