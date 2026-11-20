"""baseline — โครงสร้างฐานข้อมูล ณ 20 พ.ย. 2569 (สำเนา app/schema.sql ณ วันนั้น ใน 0001_baseline.sql)

คำสั่งทั้งหมดเป็นแบบ IF NOT EXISTS จึงใช้กับฐานข้อมูลที่สร้างก่อนมี Alembic ได้ (alembic upgrade head บันทึกรุ่นให้)
"""
from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql((Path(__file__).with_suffix(".sql")).read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("baseline ย้อนกลับไม่ได้ — กู้จากไฟล์สำรองแทน")
