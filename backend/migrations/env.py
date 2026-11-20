"""Alembic environment — ใช้ DATABASE_URL ของแอป หรือ connection ที่ส่งมาทาง config.attributes["connection"]"""
from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings

config = context.config


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


conn = config.attributes.get("connection")
if conn is not None:
    run(conn)
else:
    engine = create_engine(get_settings().database_url, future=True)
    with engine.begin() as c:
        run(c)
