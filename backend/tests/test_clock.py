"""วันนี้ต้องเป็นวันตามเวลาไทย ไม่ใช่ UTC ของ container"""
from datetime import UTC, date, datetime

from app import clock


class _Fixed(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 21, 20, 30, tzinfo=UTC).astimezone(tz)   # 03:30 น. วันที่ 22 ตามเวลาไทย


def test_today_uses_thai_time_not_utc(monkeypatch):
    monkeypatch.setattr(clock, "datetime", _Fixed)
    assert clock.today() == date(2026, 11, 22)
    assert clock.now().utcoffset().total_seconds() == 7 * 3600
