from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def get_conn() -> Iterator[Connection]:
    """FastAPI dependency: 1 transaction ต่อ 1 คำขอ (commit เมื่อสำเร็จ, rollback เมื่อ error)"""
    with get_engine().begin() as conn:
        yield conn


def init_schema(conn: Connection) -> None:
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.exec_driver_sql(sql)


def reset_schema(conn: Connection) -> None:
    conn.execute(text(
        "DROP TABLE IF EXISTS audit_event, search_chunk, citation_link, case_document, court_proceeding, "
        "court_case_owner, court_case, appeal_request, appeal_milestone, appeal_step, appeal_owner, appeal, holiday, workflow_step_def, legal_relation, "
        "provision_version, provision, legal_instrument CASCADE"
    ))
    init_schema(conn)


def rows(conn: Connection, sql: str, **params) -> list[dict]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def one(conn: Connection, sql: str, **params) -> dict | None:
    r = conn.execute(text(sql), params).mappings().first()
    return dict(r) if r else None
