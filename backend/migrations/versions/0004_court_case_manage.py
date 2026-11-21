"""court_case: เพิ่ม/แก้ไขจากหน้าเว็บ, ประวัติคดี, นำเข้า Excel, แนบเอกสาร

Revision ID: 0004_court_case_manage
Revises: 0003_law_upload_workflow
"""
from pathlib import Path

from alembic import op

revision = "0004_court_case_manage"
down_revision = "0003_law_upload_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS case_import")
    op.execute("DROP TABLE IF EXISTS court_case_revision")
    op.execute("DROP FUNCTION IF EXISTS court_case_revision_immutable()")
    for col in ("sha256", "original_name", "status", "method", "error", "added_by", "added_at"):
        op.execute(f"ALTER TABLE case_document DROP COLUMN IF EXISTS {col}")
    for col in ("row_version", "created_by", "created_at", "updated_by", "updated_at"):
        op.execute(f"ALTER TABLE court_case DROP COLUMN IF EXISTS {col}")
