"""วันที่/เวลาตามเวลาประเทศไทย

container ไม่ได้ตั้ง TZ จึงเป็น UTC — ถ้าใช้ date.today() ช่วง 00:00–07:00 น. ระบบจะถือว่ายังเป็นเมื่อวาน
(นับวัน SLA และค้นตัวบท ณ วันที่ คลาดไป 1 วัน) ทุกจุดที่ต้องการ "วันนี้" ให้เรียก today() จากที่นี่
ประเทศไทยไม่มีเวลาออมแสง จึงใช้ UTC+7 คงที่ได้โดยไม่ต้องพึ่งฐานข้อมูลเขตเวลาของระบบปฏิบัติการ
"""
from datetime import UTC, date, datetime, timedelta, timezone

TH = timezone(timedelta(hours=7), "Asia/Bangkok")


def now() -> datetime:
    return datetime.now(UTC).astimezone(TH)


def today() -> date:
    return now().date()
