"""กรอบเวลาทั้งหมดของเรื่องอุทธรณ์/ร้องเรียน (คำนวณที่ server เท่านั้น)

1. ยื่นอุทธรณ์ทันกำหนดหรือไม่  — 30 วันนับแต่ได้รับแจ้งคำสั่ง/วันที่กฎประกาศใช้ (ม.121 วรรคสอง)
2. กรอบเวลารวม                  — อุทธรณ์ 90 วันนับแต่วันรับอุทธรณ์ (ม.49 พ.ร.บ.จัดตั้งศาลปกครองฯ) หยุดนับเมื่อ กกพ. มีมติ
                                   เรื่องร้องเรียนใช้กรอบตามระเบียบ/ประกาศ/สัญญา (sla_days) ถ้าไม่ระบุ = ไม่นับ
3. คณะอนุกรรมการฯ              — 30 วันนับแต่ประชุมครั้งแรก ขยายได้ไม่เกิน 30 วัน (ข้อ 19 ระเบียบฯ, ม.45 วิ.ปกครอง)
4. กกพ.                        — 30 วันนับแต่ได้รับเรื่องจากคณะอนุกรรมการฯ
5. กรอบควบคุมภายในรายขั้น       — ตาราง ข้อ 6 ของ WI-EFL-04 (วันทำการ/วันปฏิทิน)
"""
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .legal import CONSUMER_DECISION_DAYS, CONSUMER_MEDIATION_DAYS, STEP_NOTES, STEP_REFS, ground, refs

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
    refs: list = field(default_factory=list)   # ตัวบทที่เป็นฐาน [{label, eli}] คลิกไปดูได้


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

    g = ground(a.get("appeal_ground"))
    consumer = a["case_type"] == "COMPLAINT" and a.get("complaint_basis") == "CONSUMER"

    # 1) ยื่นทันกำหนด (กำหนดตามฐานการอุทธรณ์ ข้อ 9 ระเบียบอุทธรณ์ 2552)
    if is_appeal:
        label = f"ยื่นอุทธรณ์ภายใน {g.filing_days} วัน"
        basis = " · ".join(refs_label for refs_label in (r["label"] for r in refs(*g.refs)))
        if a.get("notice_date") and a.get("received_date"):
            limit = a["notice_date"] + timedelta(days=g.filing_days)
            ok = a["received_date"] <= limit
            out.append(Clock("filing", label, basis, "DONE_ON_TIME" if ok else "DONE_LATE", a["notice_date"], limit,
                             a["received_date"], None, (a["received_date"] - a["notice_date"]).days, refs(*g.refs)))
        else:
            out.append(Clock("filing", label, basis, "NOT_STARTED", refs=refs(*g.refs)))

    # 1.1) เรื่องร้องเรียนผู้ใช้พลังงาน: ไกล่เกลี่ยให้เสร็จภายใน 30 วันนับแต่ได้รับเรื่อง (ข้อ 16) — หยุดเมื่อแสวงหาข้อเท็จจริงเสร็จ
    if consumer:
        start = a.get("received_date")
        out.append(clock("mediation", f"ไกล่เกลี่ย {CONSUMER_MEDIATION_DAYS} วัน", "ข้อ 16 ระเบียบร้องเรียน 2553", start,
                         start + timedelta(days=CONSUMER_MEDIATION_DAYS) if start else None, ms.get("FACTS_DONE"),
                         today, 7))
        out[-1].refs = refs("cmp16")

    # 2) กรอบเวลารวม
    total_refs: list = []
    if is_appeal:
        days, basis = APPEAL_TOTAL_DAYS, "WI-EFL-04 ข้อ 6 · เทียบ ม.49 พ.ร.บ.จัดตั้งศาลปกครองฯ"
        total_refs = refs("court49")
    elif consumer:
        days, basis = CONSUMER_DECISION_DAYS, "ข้อ 19 ระเบียบร้องเรียน 2553 (เกินกำหนดต้องแจ้งเหตุล่าช้า)"
        total_refs = refs("cmp19")
    else:
        days, basis = a.get("sla_days"), "ตามระเบียบ/ประกาศ/สัญญาซื้อขายไฟฟ้า · พ.ร.บ.อำนวยความสะดวกฯ"
    if days is None:
        out.append(Clock("total", "กรอบเวลารวม", basis, "NOT_APPLICABLE"))
    else:
        start = a.get("received_date")
        due = start + timedelta(days=days) if start else None
        out.append(clock("total", f"กรอบเวลารวม {days} วัน", basis, start, due, ms.get("ERC_RESOLUTION"), today,
                         near_due_days))
        out[-1].refs = total_refs

    # 3) คณะอนุกรรมการฯ
    if a.get("route") != "DIRECT":
        start = ms.get("SUBC_FIRST_MEETING")
        due = ms.get("SUBC_EXTENDED_DUE") or (start + timedelta(days=SUBC_DAYS) if start else None)
        label = "คณะอนุกรรมการฯ 30 วัน" + (" (ขยายแล้ว)" if ms.get("SUBC_EXTENDED_DUE") else "")
        out.append(clock("subcommittee", label, "ข้อ 19 ระเบียบอุทธรณ์ 2552 · ขยายเวลา: ม.45 พ.ร.บ.วิธีปฏิบัติราชการทางปกครอง",
                         start, due, ms.get("SUBC_REPORT"), today, 7))
        out[-1].refs = refs("reg19", "apa45") if is_appeal else []
        if not is_appeal:
            out[-1].basis = "WI-EFL-04 ข้อ 5.5"

    # 4) กกพ.
    start = ms.get("ERC_RECEIVED")
    out.append(clock("erc", "กกพ. พิจารณา 30 วัน", "WI-EFL-04 ข้อ 5.6", start,
                     start + timedelta(days=ERC_DAYS) if start else None, ms.get("ERC_RESOLUTION"), today, 7))

    # 5) กำหนดแจ้งผลเฉพาะ เช่น อุทธรณ์คำสั่งพักใช้/เพิกถอนใบอนุญาต: แจ้งคำชี้ขาดภายใน 15 วัน (ประกาศ 2555 ข้อ 14)
    if is_appeal and g.notify_days:
        start = ms.get("ERC_RESOLUTION")
        out.append(clock("notify", f"แจ้งคำชี้ขาดภายใน {g.notify_days} วัน", " · ".join(r["label"] for r in refs(*g.notify_refs)),
                         start, start + timedelta(days=g.notify_days) if start else None, ms.get("NOTIFY_SENT"), today, 3))
        out[-1].refs = refs(*g.notify_refs)
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


def _step_refs(code: str, case: dict | None) -> tuple[list, str | None]:
    """ฐานทางกฎหมายของขั้นตอน — อุทธรณ์ใช้ระเบียบอุทธรณ์ 2552; เรื่องร้องเรียนผู้ใช้พลังงานใช้ระเบียบร้องเรียน 2553"""
    if case is None or case.get("case_type") == "APPEAL":
        return refs(*STEP_REFS.get(code, ())), STEP_NOTES.get(code)
    if case.get("complaint_basis") == "CONSUMER" and code == "SCREEN":
        return refs("act100", "cmp16_1"), None
    return [], None


def step_clocks(steps: list[dict], ms: dict[str, date], today: date, cal: DayCountStrategy,
                case: dict | None = None) -> list[dict]:
    """เติม due_date และสถานะกรอบควบคุมภายในให้แต่ละขั้น"""
    by_no = {s["step_no"]: s for s in steps}
    out = []
    for sd in STEPS:
        s = dict(by_no[sd.step_no])
        due, anchor = step_due(sd, ms, cal) if s["state"] in ("current", "completed") else (None, None)
        s.update(code=sd.code, name=sd.name_th, limit_text=sd.limit_text, basis=sd.basis, optional=sd.optional,
                 due_date=due, anchor=anchor)
        s["refs"], s["legal_note"] = _step_refs(sd.code, case)
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
