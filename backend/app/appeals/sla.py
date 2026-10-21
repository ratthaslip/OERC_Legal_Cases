"""SLA calculator (SDD 5.1 ข) — คำนวณที่ server เท่านั้น"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol


class DayCountStrategy(Protocol):
    def add_days(self, start: date, n: int) -> date: ...
    def days_between(self, start: date, end: date) -> int: ...


class CalendarDays:
    def add_days(self, start: date, n: int) -> date:
        return start + timedelta(days=n)

    def days_between(self, start: date, end: date) -> int:
        return (end - start).days


class BusinessDays:
    """วันทำการ: ไม่นับเสาร์-อาทิตย์และวันหยุดที่กำหนด (แหล่งปฏิทินวันหยุดรอตัดสิน — SDD หัวข้อ 9)"""

    def __init__(self, holidays: set[date] | None = None) -> None:
        self.holidays = holidays or set()

    def _is_workday(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def add_days(self, start: date, n: int) -> date:
        d, left = start, n
        while left > 0:
            d += timedelta(days=1)
            if self._is_workday(d):
                left -= 1
        return d

    def days_between(self, start: date, end: date) -> int:
        sign = 1 if end >= start else -1
        a, b = (start, end) if sign == 1 else (end, start)
        n, d = 0, a
        while d < b:
            d += timedelta(days=1)
            if self._is_workday(d):
                n += 1
        return sign * n


@dataclass
class SlaResult:
    state: str               # ON_TRACK | NEAR_DUE | OVERDUE | CLOSED | NOT_APPLICABLE | INCOMPLETE_DATA
    due_date: date | None
    days_left: int | None
    days_used: int | None


def evaluate(status: str, received_date: date | None, sla_days: int | None, today: date,
             near_due_days: int, strategy: DayCountStrategy, closed_date: date | None = None) -> SlaResult:
    if sla_days is None:
        return SlaResult("NOT_APPLICABLE", None, None, None)
    if received_date is None:
        return SlaResult("INCOMPLETE_DATA", None, None, None)
    due = strategy.add_days(received_date, sla_days)
    if status == "CLOSED":
        used = strategy.days_between(received_date, closed_date) if closed_date else None
        return SlaResult("CLOSED", due, None, used)
    left = strategy.days_between(today, due)
    used = strategy.days_between(received_date, today)
    if left < 0:
        return SlaResult("OVERDUE", due, left, used)
    if left <= near_due_days:
        return SlaResult("NEAR_DUE", due, left, used)
    return SlaResult("ON_TRACK", due, left, used)
