"""อินเทอร์เฟซที่โมดูลอื่นใช้อ่านข้อมูลกฎหมาย (แทนการ join ข้ามโมดูลตรง ๆ)
ถ้าภายหลังแยก law-service ออกไป ให้เปลี่ยนไฟล์นี้เป็น HTTP client โดยโมดูลอื่นไม่ต้องแก้"""
from sqlalchemy.engine import Connection

from ..db import rows


def provision_labels(conn: Connection, eli_ids: list[str]) -> dict[str, dict]:
    if not eli_ids:
        return {}
    data = rows(conn, """SELECT p.eli_id, p.provision_no, i.short_title, i.title_th
                         FROM provision p JOIN legal_instrument i USING (instrument_id)
                         WHERE p.eli_id = ANY(:e)""", e=list(set(eli_ids)))
    return {d["eli_id"]: {**d, "label": f"{d['short_title']} {d['provision_no']}"} for d in data}
