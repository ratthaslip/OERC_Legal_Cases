"""law_upload: ส่งตรวจ, คำขอแก้ไข, ประวัติร่าง (law_upload_revision แก้/ลบไม่ได้)

Revision ID: 0003_law_upload_workflow
Revises: 0002_audit_append_only
"""
from pathlib import Path

from alembic import op

revision = "0003_law_upload_workflow"
down_revision = "0002_audit_append_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS law_upload_revision")
    op.execute("DROP FUNCTION IF EXISTS law_upload_revision_immutable()")
    op.execute("DROP INDEX IF EXISTS law_upload_one_open_correction")
    for col in ("submitted_by", "submitted_at", "amends_eli", "amends_upload"):
        op.execute(f"ALTER TABLE law_upload DROP COLUMN IF EXISTS {col}")
