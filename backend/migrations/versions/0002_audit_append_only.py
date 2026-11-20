"""audit_event: ห้าม UPDATE / DELETE / TRUNCATE (trigger)

Revision ID: 0002_audit_append_only
Revises: 0001_baseline
"""
from pathlib import Path

from alembic import op

revision = "0002_audit_append_only"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event")
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event")
    op.execute("DROP FUNCTION IF EXISTS audit_event_immutable()")
