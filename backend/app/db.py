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
        from .audit_sink import install
        install(_engine)   # สำเนา audit log ออกนอกฐานเมื่อ commit
    return _engine


def get_conn() -> Iterator[Connection]:
    """FastAPI dependency: 1 transaction ต่อ 1 คำขอ (commit เมื่อสำเร็จ, rollback เมื่อ error)"""
    with get_engine().begin() as conn:
        yield conn


BACKEND = Path(__file__).resolve().parents[1]


def alembic_config(conn: Connection | None = None):
    from alembic.config import Config
    cfg = Config(str(BACKEND / "alembic.ini"))
    if conn is not None:
        cfg.attributes["connection"] = conn
    return cfg


def init_schema(conn: Connection) -> None:
    """สร้างตารางจาก schema.sql (โครงสร้างล่าสุด) แล้วบันทึกรุ่น Alembic เป็น head
    — ฐานข้อมูลที่มีอยู่แล้วบนระบบจริงให้ใช้ `alembic upgrade head` แทน"""
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.exec_driver_sql(sql)
    from alembic import command
    command.stamp(alembic_config(conn), "head")


def reset_schema(conn: Connection) -> None:
    conn.execute(text(
        "DROP TABLE IF EXISTS audit_event, search_chunk, law_register, citation_link, case_document, court_proceeding, "
        "court_case_owner, court_case, appeal_request, appeal_milestone, appeal_step, appeal_owner, appeal, holiday, workflow_step_def, legal_relation, "
        "provision_version, provision, legal_instrument, law_open_issue, raw_file_link, law_relation CASCADE"  # law_import_log, raw_file ตั้งใจเก็บไว้ (ประวัติ/ทะเบียนไฟล์จริง)
    ))
    init_schema(conn)


def rows(conn: Connection, sql: str, **params) -> list[dict]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def one(conn: Connection, sql: str, **params) -> dict | None:
    r = conn.execute(text(sql), params).mappings().first()
    return dict(r) if r else None
