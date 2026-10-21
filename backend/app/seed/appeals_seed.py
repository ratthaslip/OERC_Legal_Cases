"""สร้างข้อมูลสาธิตงานอุทธรณ์/ร้องเรียน โดยเดินตาม workflow จริงผ่าน service (ไม่เขียนสถานะลงตารางตรง ๆ)

แต่ละเรื่องเริ่มจากวันรับเรื่อง แล้วเดินทีละเหตุการณ์ตามระยะเวลาใน WI-EFL-04 (มีความล่าช้าสุ่มบ้าง)
หยุดเมื่อเหตุการณ์ถัดไปเลยวันนี้ → เรื่องกระจายอยู่ทุกขั้นตามอายุเรื่องโดยธรรมชาติ
"""
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..appeals import service as svc
from ..appeals.sla import BusinessDays

CAL = BusinessDays()
ACTOR_REG, ACTOR_DIR = "stf1", "dir1"


@dataclass
class Plan:
    screening: str = "ACCEPTED"           # ACCEPTED | NEED_CORRECTION | REJECTED
    route: str = "SUBCOMMITTEE"           # SUBCOMMITTEE | DIRECT
    route_reason: str | None = None
    extend_subc: bool = False
    slow: float = 1.0                     # ตัวคูณความล่าช้า (>1 = ช้ากว่ากรอบ)
    info_requests: int = 1
    unanswered_request: bool = False      # หนังสือขอข้อมูลที่ไม่ได้รับคำตอบ (ค้างเกินกำหนด)
    result: str = "ยกอุทธรณ์ (ยืนตามคำสั่ง)"
    officer: str = "lgl1"
    stop_at: int | None = None            # หยุดก่อนปิดขั้นนี้ (บังคับให้ค้างอยู่ขั้นนี้)
    unassigned: bool = False


@dataclass
class Sim:
    conn: Connection
    aid: int
    today: date
    rng: random.Random
    d: date
    done: bool = False
    log: list = field(default_factory=list)

    def at(self, days: int, bd: bool = True) -> bool:
        """เลื่อนเวลาไปข้างหน้า คืน False ถ้าเลยวันนี้ (ต้องหยุด)"""
        nd = CAL.add_days(self.d, max(days, 0)) if bd else self.d + timedelta(days=max(days, 0))
        if nd > self.today:
            self.done = True
            return False
        self.d = nd
        return True


def simulate(conn: Connection, data: dict, plan: Plan, today: date, rng: random.Random) -> dict:
    aid = svc.register(conn, ACTOR_REG, data, today)
    s = Sim(conn, aid, today, rng, data["received_date"])
    ct = data["case_type"]

    def j(base: int) -> int:  # จำนวนวันพร้อมความล่าช้าสุ่ม
        return max(0, round(base * plan.slow * rng.uniform(0.6, 1.2)))

    def run():
        # ขั้น 1 มอบหมาย
        if plan.unassigned or plan.stop_at == 1 or not s.at(j(1)):
            return
        svc.assign(conn, ACTOR_DIR, aid, plan.officer, s.d, None, data.get("esarabun_no"))
        # ขั้น 2 ตรวจ
        if plan.stop_at == 2 or not s.at(j(2)):
            return
        late = plan.screening == "REJECTED"
        checks = {"standing": True, "jurisdiction": not (late and ct == "COMPLAINT"),
                  "docs": {k: plan.screening != "NEED_CORRECTION" or k != "grounds" for k, _ in svc.REQUIRED_DOCS[ct]},
                  "timely": True, "prior_steps_done": True,
                  "note": None}
        payload = {"checks": checks, "result": plan.screening,
                   "register_no": f"FM-EFL-01/{data['received_date'].year + 543}-{aid:03d}"}
        if plan.screening == "REJECTED":
            payload.update(route=plan.route, note="ยื่นพ้นกำหนดระยะเวลา/ไม่อยู่ในอำนาจ จึงเสนอความเห็นไม่รับไว้พิจารณา",
                           route_reason=plan.route_reason)
        svc.complete_step(conn, plan.officer, aid, 2, s.d, payload, None, today)
        # ขั้น 3 แจ้งแก้ไข
        if plan.screening == "NEED_CORRECTION":
            if not s.at(j(1)):
                return
            svc.add_request(conn, plan.officer, aid, {
                "target_type": "APPELLANT", "target_name": data["party_label"], "issued_date": s.d,
                "subject": "ขอให้ระบุข้อโต้แย้ง ข้อเท็จจริง/ข้อกฎหมาย และส่งพยานหลักฐานประกอบให้ครบถ้วน",
                "doc_no": f"กกพ 0101/{rng.randint(1000, 9999)}"}, None, today)
            if plan.stop_at == 3 or not s.at(j(6), bd=False):
                return
            rid = conn.execute(text("SELECT max(request_id) FROM appeal_request WHERE appeal_id=:i"), {"i": aid}).scalar()
            svc.respond_request(conn, aid, rid, s.d, "ได้รับเอกสารเพิ่มเติมครบถ้วน", None, today)
            svc.complete_step(conn, plan.officer, aid, 3, s.d, {}, None, today)
        # ขั้น 4 ข้อเท็จจริง
        if plan.screening != "REJECTED":
            for k in range(plan.info_requests):
                if not s.at(j(1)):
                    return
                internal = k % 2 == 0
                svc.add_request(conn, plan.officer, aid, {
                    "target_type": "INTERNAL" if internal else "APPELLANT",
                    "target_name": "สำนักกำกับกิจการไฟฟ้า" if internal else data["party_label"],
                    "issued_date": s.d,
                    "subject": "ขอข้อเท็จจริงและเอกสารประกอบการพิจารณาคำสั่งที่ถูกอุทธรณ์" if internal
                    else "ขอให้ชี้แจงข้อเท็จจริงเพิ่มเติมและส่งเอกสารหลักฐาน",
                    "doc_no": f"{'บันทึกข้อความ' if internal else 'กกพ 0101'}/{rng.randint(1000, 9999)}"}, None, today)
            if plan.unanswered_request or plan.stop_at == 4:
                return
            if plan.info_requests and not s.at(j(5)):
                return
            for r in svc.rows(conn, "SELECT request_id FROM appeal_request WHERE appeal_id=:i AND responded_date IS NULL",
                              i=aid):
                svc.respond_request(conn, aid, r["request_id"], s.d, "ได้รับข้อมูลแล้ว", None, today)
            if not s.at(j(1)):
                return
            svc.complete_step(conn, plan.officer, aid, 4, s.d, {"route": plan.route, "route_reason": plan.route_reason,
                                                               "note": "สรุปข้อเท็จจริง ข้อกฎหมาย และความเห็นเบื้องต้น"},
                              None, today)
        # ขั้น 5 อนุกรรมการ
        if plan.route == "SUBCOMMITTEE":
            if not s.at(j(8), bd=False):
                return
            svc.add_milestone(conn, plan.officer, aid, "SUBC_FIRST_MEETING", s.d, "ประชุมคณะอนุกรรมการฯ ครั้งแรก", None,
                              today)
            first = s.d
            if plan.extend_subc:
                if not s.at(25, bd=False):
                    return
                svc.add_milestone(conn, plan.officer, aid, "SUBC_EXT_NOTICE", s.d,
                                  "แจ้งผู้อุทธรณ์ขยายระยะเวลาพิจารณา (ม.45 วิ.ปกครอง)", None, today,
                                  first + timedelta(days=55))
            if plan.stop_at == 5 or not s.at(j(40 if plan.extend_subc else 21), bd=False):
                return
            if s.d < first:
                s.d = first
            svc.complete_step(conn, plan.officer, aid, 5, s.d, {"note": "คณะอนุกรรมการฯ มีความเห็นเสนอ กกพ."}, None, today)
        # ขั้น 6 วาระ กกพ.
        if plan.stop_at == 6 or not s.at(j(2)):
            return
        svc.complete_step(conn, plan.officer, aid, 6, s.d, {"agenda_sent_date": s.d}, None, today)
        # ขั้น 7 กกพ. มีมติ
        if plan.stop_at == 7 or not s.at(j(18), bd=False):
            return
        svc.complete_step(conn, plan.officer, aid, 7, s.d, {"result": plan.result,
                                                           "meeting_ref": f"ครั้งที่ {rng.randint(1, 48)}/{s.d.year + 543}"},
                          None, today)
        # ขั้น 8 แจ้งผล
        if plan.stop_at == 8 or not s.at(j(2)):
            return
        svc.complete_step(conn, plan.officer, aid, 8, s.d, {"rights_notice": True,
                                                           "note": "ส่งไปรษณีย์ลงทะเบียนตอบรับ"}, None, today)
        if not s.at(j(5), bd=False):
            return
        svc.add_milestone(conn, plan.officer, aid, "NOTIFY_RECEIVED", s.d, "ใบตอบรับไปรษณีย์", None, today)
        # ขั้น 9 ปิดเรื่อง
        if plan.stop_at == 9 or not s.at(j(1)):
            return
        svc.complete_step(conn, plan.officer, aid, 9, s.d, {"note": "จัดเก็บหลักฐานครบถ้วน"}, None, today)

    run()
    a = svc.one(conn, "SELECT * FROM appeal WHERE appeal_id=:i", i=aid)
    return {"appeal_id": aid, "status": a["status"], "result": a["decision_result"], "appeal_no": a["appeal_no"],
            "case_type": ct}


def norm(no: str) -> str:
    return re.sub(r"[\s.]", "", no)
