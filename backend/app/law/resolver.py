"""Point-in-time resolver (SDD 5.1 ก)

ตอบคำถาม "ตัวบทมาตรานี้ ณ วันที่ X เป็นรุ่นไหน" — ไม่มี fallback ไปรุ่นล่าสุดเหมือนต้นแบบ UI
"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection


class NotYetInForce(Exception):
    pass


class Repealed(Exception):
    pass


@dataclass
class VersionWindow:
    version_no: int
    valid_from: date
    valid_to: date | None  # None = ยังมีผลถึงปัจจุบัน

    def is_effective_on(self, as_of: date) -> bool:
        return self.valid_from <= as_of and (self.valid_to is None or as_of <= self.valid_to)


def pick_version(versions: list[VersionWindow], as_of: date) -> VersionWindow:
    """ตรรกะล้วน (ไม่แตะฐานข้อมูล) ใช้ทดสอบและใช้ร่วมกับ citation/RAG"""
    matches = [v for v in versions if v.is_effective_on(as_of)]
    if len(matches) > 1:  # schema กันไว้แล้วด้วย exclusion constraint; ถ้าเกิดแปลว่าข้อมูลเสีย
        raise ValueError("ช่วงวันที่มีผลของรุ่นซ้อนกัน")
    if matches:
        return matches[0]
    if not versions or as_of < min(v.valid_from for v in versions):
        raise NotYetInForce()
    raise Repealed()


RESOLVE_SQL = """
SELECT pv.*, p.eli_id, p.provision_no
FROM provision_version pv JOIN provision p USING (provision_id)
WHERE p.eli_id = :eli AND pv.status = 'approved'
  AND pv.valid_from <= :as_of AND (pv.valid_to IS NULL OR :as_of <= pv.valid_to)
"""


def resolve(conn: Connection, eli_id: str, as_of: date) -> dict:
    row = conn.execute(text(RESOLVE_SQL), {"eli": eli_id, "as_of": as_of}).mappings().first()
    if row:
        return dict(row)
    first = conn.execute(
        text("""SELECT min(valid_from) FROM provision_version pv JOIN provision p USING (provision_id)
                WHERE p.eli_id = :eli AND pv.status = 'approved'"""),
        {"eli": eli_id},
    ).scalar()
    if first is None or as_of < first:
        raise NotYetInForce()
    raise Repealed()
