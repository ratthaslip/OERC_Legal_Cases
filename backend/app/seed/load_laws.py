"""นำเข้าตัวบทจริงจากไฟล์ JSON (แทนข้อความสาธิต)

ใช้งาน:  python -m app.seed.load_laws laws.json [--replace]

รูปแบบไฟล์ (ตัวอย่างอยู่ใน laws.example.json):
[
  {
    "id": "eli/th/act/2550/energy-industry",        ← ELI ของตราสาร (ไม่ซ้ำ)
    "type": "พระราชบัญญัติ",
    "title": "พระราชบัญญัติการประกอบกิจการพลังงาน พ.ศ. 2550",
    "code": "พ.ร.บ. 2550",                          ← ชื่อย่อที่ใช้แสดงในการอ้างอิง
    "issuer": "รัฐสภา", "category": "กิจการพลังงานทั่วไป",
    "publishedDate": "2007-12-10", "effectiveDate": "2007-12-11",   ← ค.ศ. (ปี พ.ศ. จะถูกแปลงอัตโนมัติ)
    "provisions": [
      {
        "id": "eli/th/act/2550/energy-industry/sec-1", "number": "มาตรา 1", "part": "บททั่วไป",
        "versions": [
          {"version": 1, "validFrom": "2007-12-11", "validTo": null,
           "amendingInstrument": "ตัวบทตั้งต้น", "summary": "…", "text": "…ข้อความตามราชกิจจานุเบกษา…",
           "relations": [{"type": "ถูกแก้ไขโดย", "target": "…"}]}
        ]
      }
    ]
  }
]
ตัวบทที่นำเข้าด้วยวิธีนี้ถูกบันทึกเป็น is_demo = FALSE — ต้องผ่านการตรวจโดยนิติกรก่อนนำเข้า
"""
import json
import sys
from pathlib import Path

from sqlalchemy import text

from ..db import get_engine
from ..search.indexer import reindex_laws
from .seed import load_laws


def main(path: str, replace: bool) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    with get_engine().begin() as conn:
        for inst in data:
            exists = conn.execute(text("SELECT instrument_id FROM legal_instrument WHERE eli_id = :e"),
                                  {"e": inst["id"]}).scalar()
            if exists and not replace:
                sys.exit(f"มีตราสาร {inst['id']} อยู่แล้ว (ใช้ --replace เพื่อแทนที่)")
            if exists:
                conn.execute(text("""DELETE FROM legal_relation WHERE version_id IN (
                                       SELECT version_id FROM provision_version JOIN provision USING (provision_id)
                                       WHERE instrument_id = :i)"""), {"i": exists})
                conn.execute(text("""DELETE FROM provision_version WHERE provision_id IN (
                                       SELECT provision_id FROM provision WHERE instrument_id = :i)"""), {"i": exists})
                conn.execute(text("DELETE FROM provision WHERE instrument_id = :i"), {"i": exists})
                conn.execute(text("DELETE FROM legal_instrument WHERE instrument_id = :i"), {"i": exists})
        load_laws(conn, data)
        conn.execute(text("UPDATE legal_instrument SET is_demo = FALSE WHERE eli_id = ANY(:e)"),
                     {"e": [i["id"] for i in data]})
        n = reindex_laws(conn)
    print(f"นำเข้า {len(data)} ตราสาร, index ตัวบท {n} รุ่น")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], "--replace" in sys.argv)
