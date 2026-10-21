"""Workflow งานอุทธรณ์และเรื่องร้องเรียนตามคู่มือปฏิบัติงาน WI-EFL-04 (ตรรกะล้วน ทดสอบได้โดยไม่ใช้ฐานข้อมูล)

9 ขั้นตามตารางกำหนดระยะเวลา (ข้อ 6) พร้อมทางแยก
- ขั้น 2 ผลตรวจ: ACCEPTED → ข้ามขั้น 3 ไปขั้น 4 · NEED_CORRECTION → ขั้น 3 · REJECTED → ข้ามขั้น 3–4 เสนอไม่รับ (ข้อ 5.3(3.1))
- ขั้น 4 (หรือขั้น 2 กรณีไม่รับ) เลือกเส้นทาง: SUBCOMMITTEE → ขั้น 5 · DIRECT → ข้ามขั้น 5 (มีแนวคำวินิจฉัยแล้ว/เรื่องเร่งด่วน
  ตามความเห็นประธานอนุกรรมการฯ ข้อ 5.3(3.2))
"""
from dataclasses import dataclass, field


class WorkflowError(Exception):
    pass


@dataclass(frozen=True)
class StepDef:
    step_no: int
    code: str
    name_th: str
    anchors: tuple[str, ...]      # milestone ที่ใช้เริ่มนับกรอบเวลา (ใช้ตัวแรกที่มี)
    limit_days: int
    day_type: str                 # BD | CD
    limit_text: str
    basis: str | None = None
    optional: bool = False
    output: str | None = None     # milestone ที่ถูกบันทึกเมื่อขั้นนี้เสร็จ


STEPS: list[StepDef] = [
    StepDef(1, "RECEIVE", "รับเรื่อง ลงทะเบียน และมอบหมายผู้รับผิดชอบ", ("RECEIVED",), 1, "BD",
            "ภายใน 1 วันทำการนับแต่ได้รับเรื่อง", "WI-EFL-04 ข้อ 5.1", output="ASSIGNED"),
    StepDef(2, "SCREEN", "ตรวจสิทธิ เขตอำนาจ ระยะเวลา และความครบถ้วนของเอกสาร", ("ASSIGNED",), 3, "BD",
            "ภายใน 3 วันทำการนับแต่ได้รับมอบหมาย", "WI-EFL-04 ข้อ 5.2–5.3 · ม.121 วรรคสอง", output="SCREEN_DONE"),
    StepDef(3, "CORRECTION", "แจ้งให้แก้ไข/ส่งเอกสารเพิ่มเติม (ถ้าจำเป็น)", ("SCREEN_DONE",), 2, "BD",
            "ออกหนังสือภายใน 2 วันทำการหลังตรวจพบ · ให้ตอบกลับภายใน 7 วัน", "WI-EFL-04 ข้อ 5.2(2)",
            optional=True, output="CORRECTION_DONE"),
    StepDef(4, "FACTS", "แสวงหาข้อเท็จจริงและรับฟังคู่กรณี", ("CORRECTION_DONE", "SCREEN_DONE"), 7, "BD",
            "ภายใน 7 วันทำการนับแต่ตรวจเอกสารเบื้องต้นเสร็จ", "WI-EFL-04 ข้อ 5.4", output="FACTS_DONE"),
    StepDef(5, "SUBCOMMITTEE", "คณะอนุกรรมการฯ พิจารณาให้ความเห็น", ("SUBC_FIRST_MEETING",), 30, "CD",
            "ภายใน 30 วันนับแต่เข้าที่ประชุมครั้งแรก · ขยายได้ไม่เกิน 30 วันโดยแจ้งผู้อุทธรณ์ก่อนครบกำหนด",
            "ระเบียบ กกพ. ว่าด้วยหลักเกณฑ์การอุทธรณ์ฯ ข้อ 19 · ม.45 พ.ร.บ.วิธีปฏิบัติราชการทางปกครองฯ",
            optional=True, output="SUBC_REPORT"),
    StepDef(6, "ERC_AGENDA", "จัดทำวาระเสนอ กกพ.", ("SUBC_REPORT", "FACTS_DONE", "SCREEN_DONE"), 3, "BD",
            "ภายใน 3 วันทำการนับแต่ได้รับรายงานคณะอนุกรรมการฯ", "WI-EFL-04 ข้อ 5.6(1)", output="ERC_RECEIVED"),
    StepDef(7, "ERC_DECISION", "กกพ. พิจารณาวินิจฉัย", ("ERC_RECEIVED",), 30, "CD",
            "ภายใน 30 วันนับแต่ได้รับเรื่องจากคณะอนุกรรมการฯ (รวมทั้งหมดไม่เกิน 90 วันนับแต่รับอุทธรณ์)",
            "WI-EFL-04 ข้อ 5.6 · ม.49 พ.ร.บ.จัดตั้งศาลปกครองฯ", output="ERC_RESOLUTION"),
    StepDef(8, "NOTIFY", "จัดทำและแจ้งผลคำวินิจฉัย", ("ERC_RESOLUTION",), 3, "BD",
            "ภายใน 2 วันทำการ ไม่เกิน 3 วันทำการนับแต่ กกพ. มีมติ", "WI-EFL-04 ข้อ 5.7", output="NOTIFY_SENT"),
    StepDef(9, "CLOSE", "ปิดเรื่องและจัดเก็บหลักฐาน", ("NOTIFY_RECEIVED",), 2, "BD",
            "ภายใน 2 วันทำการนับแต่มีหลักฐานการแจ้งผล", "WI-EFL-04 ข้อ 5.8", output="CLOSED"),
]
BY_NO = {s.step_no: s for s in STEPS}
BY_CODE = {s.code: s for s in STEPS}


@dataclass
class Step:
    step_no: int
    state: str  # pending | current | completed | skipped


@dataclass
class Transition:
    steps: list[Step]
    closed: bool = False
    completed_step: int | None = None
    skipped: list[int] = field(default_factory=list)


def new_steps() -> list[Step]:
    return [Step(s.step_no, "current" if s.step_no == 1 else "pending") for s in STEPS]


def current_step(steps: list[Step]) -> int | None:
    cur = [s.step_no for s in steps if s.state == "current"]
    if len(cur) > 1:
        raise WorkflowError("มีขั้นตอนที่กำลังดำเนินการมากกว่า 1 ขั้น")
    return cur[0] if cur else None


def complete(steps: list[Step], step_no: int, *, screening_result: str | None = None,
             route: str | None = None) -> Transition:
    """ปิดขั้น step_no (ต้องเป็นขั้นปัจจุบัน) และเปิดขั้นถัดไปตามทางแยก"""
    st = {s.step_no: Step(s.step_no, s.state) for s in steps}
    if current_step(list(st.values())) != step_no:
        raise WorkflowError(f"ขั้นที่ {step_no} ไม่ใช่ขั้นที่กำลังดำเนินการ")
    st[step_no].state = "completed"
    skip: list[int] = []

    if step_no == 2:
        if screening_result not in ("ACCEPTED", "NEED_CORRECTION", "REJECTED"):
            raise WorkflowError("ต้องระบุผลการตรวจ: ACCEPTED, NEED_CORRECTION หรือ REJECTED")
        if screening_result == "ACCEPTED":
            skip, nxt = [3], 4
        elif screening_result == "NEED_CORRECTION":
            nxt = 3
        else:  # REJECTED → เสนอผู้มีอำนาจมีคำสั่งไม่รับ (ผ่านอนุฯ หรือ กกพ.)
            _check_route(route)
            skip = [3, 4] + ([5] if route == "DIRECT" else [])
            nxt = 5 if route == "SUBCOMMITTEE" else 6
    elif step_no == 4:
        _check_route(route)
        skip, nxt = ([5], 6) if route == "DIRECT" else ([], 5)
    else:
        nxt = step_no + 1

    for n in skip:
        st[n].state = "skipped"
    if nxt > len(STEPS):
        return Transition(list(st.values()), closed=True, completed_step=step_no, skipped=skip)
    st[nxt].state = "current"
    return Transition(list(st.values()), completed_step=step_no, skipped=skip)


def revert(steps: list[Step]) -> tuple[list[Step], int]:
    """ย้อนกลับไปขั้นล่าสุดที่ทำเสร็จ (ข้ามขั้นที่ถูก skip) คืน (steps ใหม่, ขั้นที่เปิดกลับ)"""
    st = {s.step_no: Step(s.step_no, s.state) for s in steps}
    cur = current_step(list(st.values()))
    done = sorted(n for n, s in st.items() if s.state == "completed")
    if not done:
        raise WorkflowError("ยังไม่มีขั้นที่ทำเสร็จ ย้อนกลับไม่ได้")
    back = done[-1]
    if cur is not None:
        st[cur].state = "pending"
    # ขั้นที่ถูกข้ามหลังจากขั้นที่เปิดกลับ ให้กลับเป็น pending (ทางแยกจะถูกตัดสินใหม่)
    for n, s in st.items():
        if n > back and s.state == "skipped":
            s.state = "pending"
    st[back].state = "current"
    return list(st.values()), back


def _check_route(route: str | None) -> None:
    if route not in ("SUBCOMMITTEE", "DIRECT"):
        raise WorkflowError("ต้องเลือกเส้นทาง: SUBCOMMITTEE (เสนอคณะอนุกรรมการฯ) หรือ DIRECT (เสนอ กกพ. โดยตรง)")
