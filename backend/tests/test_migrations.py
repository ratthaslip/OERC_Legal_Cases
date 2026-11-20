"""Alembic: ฐานข้อมูลใหม่จาก `alembic upgrade head` ต้องมีโครงสร้างเหมือนที่ app/schema.sql สร้าง
(กันลืมเขียน migration เมื่อแก้ schema.sql หรือกลับกัน) และ upgrade ซ้ำบนฐานเดิมได้"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

COLS = """SELECT table_name, column_name, data_type, is_nullable, coalesce(column_default, '') AS d
          FROM information_schema.columns WHERE table_schema = 'public' AND table_name <> 'alembic_version'
          ORDER BY 1, 2"""
IDX = "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1"


@pytest.fixture()
def two_dbs():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ฐานข้อมูล")
    url = make_url(base)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    names = [f"erc_mig_{uuid.uuid4().hex[:8]}" for _ in range(2)]
    with admin.connect() as c:
        for n in names:
            c.execute(text(f'CREATE DATABASE "{n}"'))
    engines = [create_engine(url.set(database=n)) for n in names]
    yield engines
    for e in engines:
        e.dispose()
    with admin.connect() as c:
        for n in names:
            c.execute(text(f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)'))


def _shape(engine):
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(text(COLS))], [tuple(r) for r in c.execute(text(IDX))]


def test_migrations_match_schema_sql(two_dbs):
    from alembic import command

    from app.db import alembic_config, init_schema
    via_schema, via_alembic = two_dbs
    with via_schema.begin() as c:
        init_schema(c)
    with via_alembic.begin() as c:
        command.upgrade(alembic_config(c), "head")
    assert _shape(via_alembic) == _shape(via_schema)
    with via_alembic.begin() as c:   # รันซ้ำได้ ไม่มีผล
        command.upgrade(alembic_config(c), "head")
        head = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    with via_schema.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
