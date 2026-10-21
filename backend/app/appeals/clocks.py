"""กรอบเวลาทั้งหมดของเรื่องอุทธรณ์/ร้องเรียน (คำนวณที่ server เท่านั้น)

1. ยื่นอุทธรณ์ทันกำหนดหรือไม่  — 30 วันนับแต่ได้รับแจ้งคำสั่ง/วันที่กฎประกาศใช้ (ม.121 วรรคสอง)
2. กรอบเวลารวม                  — อุทธรณ์ 90 วันนับแต่วันรับอุทธรณ์ (ม.49 พ.ร.บ.จัดตั้งศาลปกครองฯ) หยุดนับเมื่อ กกพ. มีมติ
                                   เรื่องร้องเรียนใช้กรอบตามระเบียบ/ประกาศ/สัญญา (sla_days) ถ้าไม่ระบุ = ไม่นับ
3. คณะอนุกรรมการฯ              — 30 วันนับแต่ประชุมครั้งแรก ขยายได้ไม่เกิน 30 วัน (ข้อ 19 ระเบียบฯ, ม.45 วิ.ปกครอง)
4. กกพ.                        — 30 วันนับแต่ได้รับเรื่องจากคณะอนุกรรมการฯ
5. กรอบควบคุมภายในรายขั้น       — ตาราง ข้อ 6 ของ WI-EFL-04 (วันทำการ/วันปฏิทิน)
"""
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from .sla import DayCountStrategy
from .workflow import STEPS, StepDef

APPEAL_FILING_DAYS = 30
APPEAL_TOTAL_DAYS = 90
SUBC_DAYS = 30
SUBC_MAX_EXTENSION = 30
ERC_DAYS = 30


@dataclass
class Clock:
    key: str
    label: str
    basis: str
    state: str                  # ON_TRACK | NEAR_DUE | OVERDUE | DONE_ON_TIME | DONE_LATE | NOT_STARTED | NOT_APPLICABLE
    start: date | None = None
    due: date | None = None
    stop: date | None = None
    days_left: int | None = None
    days_used: int | None = None


def clock(key: str, label: str, basis: str, start: date | None, due: date | None, stop: date | None,
          today: date, near: int) -> Clock:
    if start is None or due is None:
        return Clock(key, label, basis, "NOT_STARTED", start, due, stop)
    if stop is not None:
        return Clock(key, label, basis, "DONE_ON_TIME" if stop <= due else "DONE_LATE", start, due, stop,
                     None, (stop - start).days)
    left = (due - today).days
    state = "OVERDUE" if left < 0 else "NEAR_DUE" if left <= near else "ON_TRACK"
    return Clock(key, label, basis, state, start, due, None, left, (today - start).days)


def legal_clocks(a: dict, ms: dict[str, date], today: date, near_due_days: int) -> list[dict]:
    out: list[Clock] = []
    is_appeal = a["case_type"] == "APPEAL"

    # 1) ยื่นทันกำหนด
    if is_appeal:
        if a.get("notice_date") and a.get("received_date"):
            limit = a["notice_date"] + timedelta(days=APPEAL_FILING_DAYS)
            ok = a["received_date"] <= limit
            out.append(Clock("filing", "ยื่นอุทธรณ์ภายใน 30 วัน", "ม.121 วรรคสอง พ.ร.บ.การประกอบกิจการพลังงานฯ",
                             "DONE_ON_TIME" if ok else "DONE_LATE", a["notice_date"], limit, a["received_date"],
                             None, (a["received_date"] - a["notice_date"]).days))
        else:
            out.append(Clock("filing", "ยื่นอุทธรณ์ภายใน 30 วัน", "ม.121 วรรคสอง", "NOT_STARTED"))

    # 2) กรอบเวลารวม
    if is_appeal:
        days, basis = APPEAL_TOTAL_DAYS, "ม.49 พ.ร.บ.จัดตั้งศาลปกครองฯ"
    else:
        days, basis = a.get("sla_days"), "ตามระเบียบ/ประกาศ/สัญญาซื้อขายไฟฟ้า · พ.ร.บ.อำนวยความสะดวกฯ"
    if days is None:
        out.append(Clock("total", "กรอบเวลารวม", basis, "NOT_APPLICABLE"))
    else:
        start = a.get("received_date")
        due = start + timedelta(days=days) if start else None
        out.append(clock("total", f"กรอบเวลารวม {days} วัน", basis, start, due, ms.get("ERC_RESOLUTION"), today,
                         near_due_days))

    # 3) คณะอนุกรรมการฯ
    if a.get("route") != "DIRECT":
        start = ms.get("SUBC_FIRST_MEETING")
        due = ms.get("SUBC_EXTENDED_DUE") or (start + timedelta(days=SUBC_DAYS) if start else None)
        label = "คณะอนุกรรมการฯ 30 วัน" + (" (ขยายแล้ว)" if ms.get("SUBC_EXTENDED_DUE") else "")
        out.append(clock("subcommittee", label, "ข้อ 19 ระเบียบฯ · ม.45 พ.ร.บ.วิธีปฏิบัติราชการทางปกครองฯ", start, due,
                         ms.get("SUBC_REPORT"), today, 7))

    # 4) กกพ.
    start = ms.get("ERC_RECEIVED")
    out.append(clock("erc", "กกพ. พิจารณา 30 วัน", "WI-EFL-04 ข้อ 5.6", start,
                     start + timedelta(days=ERC_DAYS) if start else None, ms.get("ERC_RESOLUTION"), today, 7))
    return [asdict(c) for c in out]


def step_due(sd: StepDef, ms: dict[str, date], cal: DayCountStrategy) -> tuple[date | None, str | None]:
    """วันครบกำหนดของขั้น และ milestone ที่ใช้เป็นจุดเริ่ม"""
    if sd.code == "SUBCOMMITTEE" and ms.get("SUBC_EXTENDED_DUE"):
        return ms["SUBC_EXTENDED_DUE"], "SUBC_EXTENDED_DUE"
    for code in sd.anchors:
        if code in ms:
            start = ms[code]
            due = cal.add_days(start, sd.limit_days) if sd.day_type == "BD" else start + timedelta(days=sd.limit_days)
            return due, code
    return None, None


def step_clocks(steps: list[dict], ms: dict[str, date], today: date, cal: DayCountStrategy) -> list[dict]:
    """เติม due_date และสถานะกรอบควบคุมภายในให้แต่ละขั้น"""
    by_no = {s["step_no"]: s for s in steps}
    out = []
    for sd in STEPS:
        s = dict(by_no[sd.step_no])
        due, anchor = step_due(sd, ms, cal) if s["state"] in ("current", "completed") else (None, None)
        s.update(code=sd.code, name=sd.name_th, limit_text=sd.limit_text, basis=sd.basis, optional=sd.optional,
                 due_date=due, anchor=anchor)
        if s["state"] == "completed":
            s["timing"] = None if due is None or s["completed_at"] is None else (
                "ON_TIME" if s["completed_at"] <= due else "LATE")
        elif s["state"] == "current":
            if due is None:
                s["timing"] = "WAITING"  # ยังไม่มีวันเริ่มนับ เช่น ยังไม่ได้นัดประชุมอนุฯ
            else:
                left = (due - today).days
                s["timing"] = "OVERDUE" if left < 0 else "NEAR_DUE" if left <= (1 if sd.day_type == "BD" else 7) else "ON_TRACK"
                s["days_left"] = left
        else:
            s["timing"] = None
        out.append(s)
    return out


def request_state(r: dict, today: date) -> str:
    if r.get("responded_date"):
        return "ANSWERED"
    left = (r["due_date"] - today).days
    return "OVERDUE" if left < 0 else "NEAR_DUE" if left <= 2 else "OPEN"
