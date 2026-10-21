"""โหลดข้อมูลสาธิตสำหรับ POC

แหล่งข้อมูล
1. prototype_data.json — ข้อมูลชุดเดียวกับต้นแบบ UI (ตราสาร 2 ฉบับ, อุทธรณ์ 4 เรื่อง, คดี 2 คดี)
2. เรื่องอุทธรณ์/คดีสมมติที่สร้างเพิ่มแบบกำหนดผลได้ (random seed คงที่) ให้ครบทุกสถานะ SLA

⚠ ข้อความตัวบทในชุดนี้เป็นข้อความสาธิต (is_demo = TRUE) ไม่ใช่ตัวบทจริงจากราชกิจจานุเบกษา
   ก่อนสาธิตกับผู้ใช้จริงให้แทนด้วยข้อมูลที่นิติกรตรวจแล้วผ่าน `python -m app.seed.load_laws <ไฟล์.json>`

ใช้งาน: python -m app.seed.seed   (ล้างฐานแล้วสร้างใหม่ทั้งหมด)
"""
import json
import random
import re
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..appeals.workflow import STEPS
from ..db import get_engine, reset_schema
from ..law.resolver import NotYetInForce, Repealed, VersionWindow, pick_version
from ..search.indexer import reindex_cases, reindex_laws
from .appeals_seed import Plan, simulate

HERE = Path(__file__).parent
TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def parse_th_date(s: str | None) -> date | None:
    """'12 ก.ย. 2567' → date(2024, 9, 12); รูปแบบอื่นคืน None"""
    if not s:
        return None
    m = re.search(r"(\d{1,2})\s*(" + "|".join(re.escape(x) for x in TH_MONTHS) + r")\s*(\d{4})", s)
    if not m:
        return None
    return date(int(m.group(3)) - 543, TH_MONTHS.index(m.group(2)) + 1, int(m.group(1)))


def parse_iso(s: str | None) -> date | None:
    """ISO ที่อาจเป็นปี พ.ศ. (ต้นแบบปน) → ค.ศ.; 9999-12-31 / '-' → None"""
    if not s or s == "-" or s.startswith("9999"):
        return None
    y, m, d = (int(x) for x in s.split("-"))
    return date(y - 543 if y > 2400 else y, m, d)


def be(d: date) -> int:
    return d.year + 543


def party_code(label: str) -> str:
    m = re.search(r"\[([A-Z0-9-]+)\]", label)
    return m.group(1) if m else "UNKNOWN"


# ---------------------------------------------------------------- law
def load_laws(conn: Connection, instruments: list[dict]) -> dict[str, list[VersionWindow]]:
    windows: dict[str, list[VersionWindow]] = {}
    for inst in instruments:
        iid = conn.execute(text("""
            INSERT INTO legal_instrument (eli_id, instrument_type, title_th, short_title, issuer, category,
                                          promulgated_date, effective_date, is_demo)
            VALUES (:eli, :t, :title, :short, :issuer, :cat, :pub, :eff, TRUE) RETURNING instrument_id"""), {
            "eli": inst["id"], "t": inst["type"], "title": inst["title"], "short": inst["code"],
            "issuer": inst["issuer"], "cat": inst["category"], "pub": parse_iso(inst["publishedDate"]),
            "eff": parse_iso(inst["effectiveDate"])}).scalar()
        for order, p in enumerate(inst["provisions"]):
            pid = conn.execute(text("""
                INSERT INTO provision (instrument_id, eli_id, provision_no, part_heading, sort_order)
                VALUES (:i, :eli, :no, :part, :o) RETURNING provision_id"""),
                {"i": iid, "eli": p["id"], "no": p["number"], "part": p["part"], "o": order}).scalar()
            windows[p["id"]] = []
            for v in p["versions"]:
                vf, vt = parse_iso(v["validFrom"]), parse_iso(v["validTo"])
                vid = conn.execute(text("""
                    INSERT INTO provision_version (provision_id, version_no, body_text, valid_from, valid_to, status,
                                                   amended_by, summary)
                    VALUES (:p, :n, :body, :vf, :vt, 'approved', :am, :sum) RETURNING version_id"""),
                    {"p": pid, "n": v["version"], "body": v["text"], "vf": vf, "vt": vt,
                     "am": v["amendingInstrument"], "sum": v["summary"]}).scalar()
                for rel in v.get("relations", []):
                    conn.execute(text("""INSERT INTO legal_relation (version_id, relation_type, target_text)
                                         VALUES (:v, :t, :x)"""), {"v": vid, "t": rel["type"], "x": rel["target"]})
                windows[p["id"]].append(VersionWindow(v["version"], vf, vt))
    return windows


# ---------------------------------------------------------------- appeals / cases helpers
def insert_case(conn: Connection, k: dict) -> int:
    cid = conn.execute(text("""
        INSERT INTO court_case (appeal_id, black_no, red_no, court_level, court_name, case_category, subject, summary,
                                party_code, party_label, defendant, energy_sector, incident_date, filed_date,
                                judgment_date, status, stage_text, result, classification)
        VALUES (:appeal_id, :black, :red, :level, :court_name, :category, :subject, :summary, :party_code,
                :party_label, :defendant, :sector, :incident, :filed, :judgment, :status, :stage, :result, :cls)
        RETURNING case_id"""), k).scalar()
    for u in k["owners"]:
        conn.execute(text("INSERT INTO court_case_owner VALUES (:c, :u)"), {"c": cid, "u": u})
    for p in k.get("proceedings", []):
        conn.execute(text("""INSERT INTO court_proceeding (case_id, proceeding_date, title, detail)
                             VALUES (:c, :d, :t, :x)"""), {"c": cid, **p})
    for d in k.get("documents", []):
        insert_doc(conn, "court_case", cid, d)
    for c in k.get("citations", []):
        insert_citation(conn, "court_case", cid, c)
    return cid


def insert_doc(conn: Connection, owner_type: str, owner_id: int, d: dict) -> None:
    conn.execute(text("""INSERT INTO case_document (owner_type, owner_id, doc_no, doc_type, doc_date, pages, excerpt)
                         VALUES (:ot, :oid, :no, :t, :d, :p, :x)"""),
                 {"ot": owner_type, "oid": owner_id, "no": d.get("doc_no"), "t": d["doc_type"],
                  "d": d.get("doc_date"), "p": d.get("pages"), "x": d["excerpt"]})


def insert_citation(conn: Connection, owner_type: str, owner_id: int, c: dict) -> None:
    conn.execute(text("""INSERT INTO citation_link (owner_type, owner_id, cited_eli_id, cited_version_no, note)
                         VALUES (:ot, :oid, :eli, :v, :note)"""),
                 {"ot": owner_type, "oid": owner_id, "eli": c["eli"], "v": c["version"], "note": c.get("note")})


def version_at(windows: dict[str, list[VersionWindow]], eli: str, when: date) -> int | None:
    try:
        return pick_version(windows[eli], when).version_no
    except (NotYetInForce, Repealed):
        return None


# ---------------------------------------------------------------- prototype data
def load_prototype(conn: Connection, data: dict, windows, today: date, rng: random.Random) -> dict[str, int]:
    """อุทธรณ์ 4 เรื่องจากต้นแบบ UI — เดินตาม workflow จริงจากวันรับเรื่อง"""
    ids: dict[str, int] = {}
    for p in data["appeals"]:
        received, incident = parse_iso(p["receivedDate"]), parse_iso(p["incidentDate"])
        closed = p["workflowStep"] == 5
        appeal_no, shift = p["appealNo"], None
        if not closed:
            # เรื่องที่ยังเปิดอยู่: เลื่อนวันที่ให้สัมพันธ์กับวันนี้ (ใกล้ครบกำหนด 90 วัน)
            new_received = today - timedelta(days=90 - p["slaDaysRemaining"])
            shift = new_received - received
            incident, received = incident + shift, new_received
            appeal_no = re.sub(r"/\d{4}", f"/{be(received)}", appeal_no)
        late = "พ้นกำหนด" in p["outcome"]
        notice = received - timedelta(days=37 if late else 14)
        plan = Plan(screening="REJECTED" if late else "ACCEPTED", route="SUBCOMMITTEE",
                    result=p["outcome"] if closed else "", officer="lgl1", info_requests=2,
                    stop_at=None if closed else 5, extend_subc=not closed)
        r = simulate(conn, {
            "case_type": "APPEAL", "target_type": "ORDER", "filing_channel": "ELECTRONIC" if received.year >= 2022 else "POST",
            "esarabun_no": f"ES-{be(received)}/{rng.randint(10000, 99999)}", "appeal_no": appeal_no,
            "received_date": received, "notice_date": notice, "incident_date": incident, "subject": p["title"],
            "summary": p["summary"], "party_code": party_code(p["appellantMasked"]), "party_label": p["appellantMasked"],
            "respondent": p["respondent"], "energy_sector": p["energySector"],
            "classification": "restricted" if p["restrictedOnly"] else "internal"}, plan, today, rng)
        aid = r["appeal_id"]
        for d in p["documents"]:
            dd = parse_th_date(d["docDate"])
            insert_doc(conn, "appeal", aid, {"doc_no": d["docId"], "doc_type": d["docType"],
                                             "doc_date": dd + shift if (shift and dd) else dd, "pages": d["pages"],
                                             "excerpt": d["excerpt"]})
        for c in p["citations"]:
            insert_citation(conn, "appeal", aid, {"eli": c["provisionId"], "version": c["versionUsed"], "note": c["note"]})
        ids[p["id"]] = aid
    return ids


def load_prototype_cases(conn: Connection, data: dict, appeal_ids: dict[str, int], extra_appeal: dict[str, int]):
    for k in data["cases"]:
        judgment = parse_iso(k["judgmentDate"])
        origin = appeal_ids.get(k["originAppealId"]) if k["originAppealId"] else extra_appeal.get(k["originAppealNo"])
        insert_case(conn, {
            "appeal_id": origin, "black": k["blackCaseNo"],
            "red": None if k["redCaseNo"] == "อยู่ระหว่างพิจารณา" else k["redCaseNo"],
            "level": k["courtLevel"], "court_name": k["courtLevel"], "category": k["caseCategory"],
            "subject": k["title"], "summary": k["summary"], "party_code": party_code(k["plaintiffMasked"]),
            "party_label": k["plaintiffMasked"], "defendant": k["defendant"], "sector": k["energySector"],
            "incident": parse_iso(k["incidentDate"]), "filed": parse_iso(k["filingDate"]), "judgment": judgment,
            "status": "FINAL" if judgment else "PENDING", "stage": k["litigationStage"], "result": k["outcome"],
            "cls": "restricted" if k["restrictedOnly"] else "internal", "owners": ["lgl1", "lgl2"],
            "proceedings": [{"d": parse_th_date(x["date"]), "t": x["step"], "x": x["detail"]}
                            for x in k["courtProceedings"] if parse_th_date(x["date"])],
            "documents": [{"doc_no": d["docId"], "doc_type": d["docType"], "doc_date": parse_th_date(d["docDate"]),
                           "pages": d["pages"], "excerpt": d["excerpt"]} for d in k["documents"]],
            "citations": [{"eli": c["provisionId"], "version": c["versionUsed"], "note": c["note"]}
                          for c in k["citations"]],
        })


# ---------------------------------------------------------------- generated (สมมติ)
SEC47, SEC75, SEC128 = "eli/th/act/2550/sec-47", "eli/th/act/2550/sec-75", "eli/th/act/2550/sec-128"
CL12 = "eli/th/reg/2565/clause-12"
LABEL = {SEC47: "มาตรา 47", SEC75: "มาตรา 75", SEC128: "มาตรา 128", CL12: "ข้อ 12 ของระเบียบ กกพ. ว่าด้วยการรับซื้อไฟฟ้าจากพลังงานหมุนเวียน พ.ศ. 2565"}

TEMPLATES = [
    dict(sector="กิจการไฟฟ้า", party=("ผู้ยื่นขอผลิตไฟฟ้า", "EL-VSPP"), cites=[SEC75, CL12, SEC47],
         subject="อุทธรณ์คำสั่งไม่รับคำขอขายไฟฟ้าโครงการ{tech}แบบ VSPP เนื่องจากจุดเชื่อมโยงเต็มศักยภาพ",
         summary="ผู้อุทธรณ์โต้แย้งผลการประเมินความสามารถรองรับของสายป้อน (Feeder Capacity) ณ จุดเชื่อมโยงที่เสนอ ตามข้อ 12 ของระเบียบ กกพ. ว่าด้วยการรับซื้อไฟฟ้าจากพลังงานหมุนเวียน พ.ศ. 2565 และยื่นอุทธรณ์ภายใน 30 วันตามมาตรา 75",
         tech=["พลังงานแสงอาทิตย์บนหลังคา", "ชีวมวล", "ก๊าซชีวภาพ", "พลังงานแสงอาทิตย์ลอยน้ำ"]),
    dict(sector="กิจการไฟฟ้า", party=("ผู้รับใบอนุญาตผลิตไฟฟ้า", "EL-SPP"), cites=[SEC128, SEC47, SEC75],
         subject="อุทธรณ์คำสั่งปรับทางปกครองกรณีไม่ส่งรายงานคุณภาพไฟฟ้าตามเงื่อนไขท้ายใบอนุญาต{tech}",
         summary="ผู้อุทธรณ์โต้แย้งคำสั่งปรับทางปกครองตามมาตรา 128 กรณีไม่ส่งรายงานผลการตรวจวัดคุณภาพไฟฟ้าตามเงื่อนไขท้ายใบอนุญาตที่กำหนดตามมาตรา 47 โดยอ้างว่าระบบตรวจวัดขัดข้องจากเหตุนอกเหนือการควบคุม",
         tech=["", " โรงไฟฟ้าระบบความร้อนร่วม", " โรงไฟฟ้าชีวมวล"]),
    dict(sector="กิจการก๊าซธรรมชาติ", party=("ผู้รับใบอนุญาตจำหน่ายก๊าซธรรมชาติ", "NG-DST"), cites=[SEC128, SEC75],
         subject="อุทธรณ์คำสั่งให้แก้ไขระบบตรวจวัดปริมาณก๊าซธรรมชาติ ณ สถานี{tech}",
         summary="ผู้อุทธรณ์โต้แย้งคำสั่งให้แก้ไขปรับปรุงระบบตรวจวัดปริมาณก๊าซภายในระยะเวลาที่กำหนดตามมาตรา 128 โดยขอขยายระยะเวลาเนื่องจากต้องนำเข้าอุปกรณ์จากต่างประเทศ และใช้สิทธิอุทธรณ์ตามมาตรา 75",
         tech=["จ่ายก๊าซนิคมอุตสาหกรรม", "ควบคุมความดัน", "รับก๊าซปลายทาง"]),
    dict(sector="กิจการระบบโครงข่ายพลังงาน", party=("ผู้รับใบอนุญาตระบบโครงข่ายไฟฟ้า", "GRID"), cites=[SEC47, SEC75],
         subject="อุทธรณ์คำสั่งกำหนดเงื่อนไขท้ายใบอนุญาตด้านความมั่นคงของระบบโครงข่าย{tech}",
         summary="ผู้อุทธรณ์โต้แย้งเงื่อนไขท้ายใบอนุญาตที่กำหนดตามมาตรา 47 ให้ต้องรักษาระดับแรงดันและสำรองกำลังผลิตเพื่อความมั่นคงของระบบโครงข่าย โดยอ้างว่าเป็นภาระเกินสมควร และยื่นอุทธรณ์ตามมาตรา 75",
         tech=["ไฟฟ้า", "ในเขตพื้นที่ภาคตะวันออก", "ระบบจำหน่ายในพื้นที่ห่างไกล"]),
    dict(sector="กิจการก๊าซธรรมชาติ", party=("ผู้รับใบอนุญาตขนส่งก๊าซธรรมชาติ", "NG-TRN"), cites=[SEC128, SEC75, SEC47],
         subject="อุทธรณ์คำสั่งพักใช้ใบอนุญาตขนส่งก๊าซธรรมชาติทางท่อ{tech}",
         summary="ผู้อุทธรณ์โต้แย้งคำสั่งพักใช้ใบอนุญาตตามมาตรา 128 กรณีไม่ปฏิบัติตามหลักเกณฑ์ด้านความปลอดภัยของระบบท่อ และขอให้พิจารณามาตรการแก้ไขแทนการพักใช้ใบอนุญาต",
         tech=["", " ช่วงสถานีควบคุมความดันย่อย"]),
    dict(sector="กิจการไฟฟ้า", party=("ผู้รับใบอนุญาตผลิตไฟฟ้า", "EL-SPP"), cites=[SEC75, SEC47],
         subject="อุทธรณ์คำสั่งไม่อนุญาตให้ขยายกำหนดวันเริ่มต้นซื้อขายไฟฟ้าเชิงพาณิชย์ (SCOD) โครงการ{tech}",
         summary="ผู้อุทธรณ์ขอให้ทบทวนคำสั่งไม่ขยายกำหนด SCOD โดยอ้างเหตุสุดวิสัยด้านการก่อสร้างระบบเชื่อมต่อ ประเด็นสำคัญคือการยื่นอุทธรณ์ภายในระยะเวลาตามมาตรา 75 ฉบับที่ใช้บังคับ ณ วันเกิดเหตุ",
         tech=["โรงไฟฟ้าชีวมวล", "โรงไฟฟ้าพลังงานลม", "โรงไฟฟ้าขยะชุมชน"]),
    dict(sector="กิจการไฟฟ้า", party=("ผู้ยื่นขอผลิตไฟฟ้า", "EL-VSPP"), cites=[CL12, SEC75],
         subject="อุทธรณ์คำสั่งถือว่าสละสิทธิเนื่องจากวางหลักประกันการยื่นขอผลิตไฟฟ้าไม่ครบถ้วน{tech}",
         summary="ผู้อุทธรณ์โต้แย้งคำสั่งที่ถือว่าสละสิทธิในการพิจารณาคัดเลือก เนื่องจากวางหลักประกันตามอัตราเมกะวัตต์ไม่ครบภายในเวลาที่กำหนดตามข้อ 12 ของระเบียบ กกพ. พ.ศ. 2565",
         tech=["", " (โครงการพลังงานแสงอาทิตย์ร่วมกับระบบกักเก็บพลังงาน)"]),
]
RESULTS = ["ยกอุทธรณ์ (ยืนตามคำสั่งสำนักงาน กกพ.)", "แก้คำสั่ง (ขยายระยะเวลาดำเนินการ)",
           "เพิกถอนคำสั่งและให้พิจารณาใหม่", "ไม่รับคำอุทธรณ์ไว้พิจารณา (ยื่นพ้นกำหนด)"]


COMPLAINTS = [
    dict(basis="PPA", sector="กิจการไฟฟ้า", party=("คู่สัญญาซื้อขายไฟฟ้า", "EL-PPA"), cites=[SEC47],
         subject="ข้อพิพาทตามสัญญาซื้อขายไฟฟ้ากรณีการคำนวณค่าปรับจากการจ่ายไฟฟ้าเข้าระบบล่าช้า",
         summary="ผู้ร้องเรียนโต้แย้งการคำนวณค่าปรับตามสัญญาซื้อขายไฟฟ้า ซึ่งสัญญากำหนดให้ กกพ. เป็นผู้วินิจฉัยชี้ขาดข้อพิพาท"),
    dict(basis="PROCUREMENT", sector="กิจการไฟฟ้า", party=("ผู้ยื่นข้อเสนอขายไฟฟ้า", "EL-RFP"), cites=[CL12],
         subject="ร้องเรียนผลการคัดเลือกตามประกาศเชิญชวนการรับซื้อไฟฟ้าจากพลังงานหมุนเวียน",
         summary="ผู้ร้องเรียนโต้แย้งผลการประเมินความพร้อมด้านระบบโครงข่ายตามข้อ 12 ของระเบียบ กกพ. ว่าด้วยการรับซื้อไฟฟ้าจากพลังงานหมุนเวียน พ.ศ. 2565"),
    dict(basis="PPA", sector="กิจการระบบโครงข่ายพลังงาน", party=("คู่สัญญาซื้อขายไฟฟ้า", "EL-PPA"), cites=[SEC47],
         subject="ข้อพิพาทตามสัญญาซื้อขายไฟฟ้ากรณีการงดรับซื้อไฟฟ้าช่วงบำรุงรักษาระบบโครงข่าย",
         summary="ผู้ร้องเรียนเห็นว่าการงดรับซื้อไฟฟ้าไม่เป็นไปตามเงื่อนไขในสัญญาซื้อขายไฟฟ้า"),
]
APPEAL_RESULTS = ["ยกอุทธรณ์ (ยืนตามคำสั่งสำนักงาน กกพ.)", "แก้คำสั่ง (ขยายระยะเวลาดำเนินการ)",
                  "เพิกถอนคำสั่งและให้พิจารณาใหม่"]
COMPLAINT_RESULTS = ["ยกเรื่องร้องเรียน", "ให้คู่สัญญาปฏิบัติตามสัญญาซื้อขายไฟฟ้า"]


def generate(conn: Connection, windows, today: date, rng: random.Random) -> tuple[list[dict], dict[str, int]]:
    made: list[dict] = []
    extra: dict[str, int] = {}

    def one_case(received: date, tpl: dict, plan: Plan, cls: str, case_type: str = "APPEAL",
                 appeal_no: str | None = None, late: bool = False) -> dict:
        notice = received - timedelta(days=rng.randint(36, 50) if late else rng.randint(4, 27))
        incident = notice - timedelta(days=rng.randint(3, 20))
        code = f"{tpl['party'][1]}-{rng.randint(10, 499):03d}"
        cites = []
        for eli in tpl["cites"]:
            v = version_at(windows, eli, incident)
            if v is not None:
                cites.append({"eli": eli, "version": v, "note": f"ตัวบทรุ่นที่ {v} ที่มีผล ณ วันเกิดเหตุ"})
        tech = rng.choice(tpl.get("tech", [""]))
        data = {
            "case_type": case_type, "complaint_basis": tpl.get("basis"), "target_type": "ORDER" if case_type == "APPEAL" else None,
            "filing_channel": rng.choice(["ELECTRONIC", "ELECTRONIC", "POST", "COUNTER"]),
            "esarabun_no": f"ES-{be(received)}/{rng.randint(10000, 99999)}", "appeal_no": appeal_no,
            "received_date": received, "notice_date": notice if case_type == "APPEAL" else None,
            "incident_date": incident, "subject": tpl["subject"].format(tech=tech), "summary": tpl["summary"],
            "party_code": code, "party_label": f"{tpl['party'][0]} รหัส [{code}]",
            "respondent": "เลขาธิการสำนักงานคณะกรรมการกำกับกิจการพลังงาน" if case_type == "APPEAL" else "คู่สัญญา/ผู้รับซื้อไฟฟ้า",
            "energy_sector": tpl["sector"], "classification": cls,
            "sla_days": None if case_type == "APPEAL" else rng.choice([60, 90, None]),
        }
        r = simulate(conn, data, plan, today, rng)
        aid = r["appeal_id"]
        insert_doc(conn, "appeal", aid, {
            "doc_type": "คำอุทธรณ์" if case_type == "APPEAL" else "หนังสือร้องเรียน", "doc_date": received,
            "pages": rng.randint(6, 20),
            "excerpt": (f"ผู้อุทธรณ์ใช้สิทธิอุทธรณ์ต่อ กกพ. {'เมื่อพ้นกำหนดสามสิบวัน' if late else 'ภายในกำหนดสามสิบวัน'} นับแต่ได้รับแจ้งคำสั่ง "
                        if case_type == "APPEAL" else "ผู้ร้องเรียนยื่นเรื่องต่อ กกพ. ตามข้อกำหนดในสัญญา/ประกาศ ") + tpl["summary"]})
        if r["status"] == "CLOSED" and r["result"]:
            insert_doc(conn, "appeal", aid, {
                "doc_type": f"{'คำวินิจฉัยอุทธรณ์' if case_type == 'APPEAL' else 'ผลการพิจารณาเรื่องร้องเรียน'} ที่ {r['appeal_no'].split(' ')[1]}",
                "doc_date": received + timedelta(days=70), "pages": rng.randint(8, 30),
                "excerpt": f"กกพ. พิจารณาแล้ว โดยอาศัย{'และ'.join(LABEL[c['eli']] for c in cites[:2]) or 'ข้อกำหนดในสัญญา'} "
                           f"จึงมีคำวินิจฉัยให้{r['result'].split(' (')[0]}"})
        for c in cites:
            insert_citation(conn, "appeal", aid, c)
        out = {**r, "received": received, "incident": incident, "subject": data["subject"], "party_code": code,
               "party_label": data["party_label"], "sector": tpl["sector"], "cls": cls, "owners": [plan.officer],
               "citations": cites}
        made.append(out)
        return out

    # เรื่องเก่าที่ปิดแล้ว (ย้อนหลังหลายปี) — มีทั้งผ่านอนุฯ / เสนอ กกพ. โดยตรง / ไม่รับเพราะยื่นพ้นกำหนด
    for i in range(18):
        received = date(2020, 3, 1) + timedelta(days=rng.randint(0, (today - date(2020, 3, 1)).days - 200))
        late = i % 7 == 3
        direct = i % 5 == 1
        cls = "restricted" if i % 5 == 2 else "internal"
        officer = "lgl2" if cls == "restricted" else rng.choice(["lgl1", "lgl2"])
        one_case(received, TEMPLATES[i % len(TEMPLATES)], Plan(
            screening="REJECTED" if late else rng.choice(["ACCEPTED", "ACCEPTED", "NEED_CORRECTION"]),
            route="DIRECT" if direct else "SUBCOMMITTEE",
            route_reason="มีแนวคำวินิจฉัยของ กกพ. ในประเด็นเดียวกันแล้ว" if direct else None,
            extend_subc=i % 6 == 4, info_requests=rng.randint(1, 2),
            result="ไม่รับอุทธรณ์ไว้พิจารณา (ยื่นพ้นกำหนด 30 วัน ตาม ม.121 วรรคสอง)" if late else rng.choice(APPEAL_RESULTS),
            officer=officer), cls, late=late)

    # เรื่องต้นทางของคดี พ. 89/2565 ในต้นแบบ
    a09 = one_case(date(2022, 4, 25), TEMPLATES[3], Plan(result="ยกอุทธรณ์ (ยืนตามคำสั่งสำนักงาน กกพ.)"),
                   "internal", appeal_no="อธ. 09/2565")
    extra["อธ. 09/2565"] = a09["appeal_id"]

    # เรื่องที่กำลังดำเนินการ: อายุเรื่องต่างกัน → กระจายทุกขั้น (บางเรื่องช้ากว่ากรอบ)
    active = [
        (0, Plan(unassigned=True)),                                          # เพิ่งรับเข้า รอ ผอ.คบ. มอบหมาย
        (3, Plan(unassigned=True)),                                          # ค้างมอบหมายเกิน 1 วันทำการ
        (2, Plan(stop_at=2)),
        (6, Plan(screening="NEED_CORRECTION", stop_at=3)),
        (12, Plan(screening="NEED_CORRECTION")),
        (10, Plan(info_requests=2, unanswered_request=True)),                # หนังสือขอข้อมูลค้างเกินกำหนด
        (16, Plan(info_requests=1, stop_at=4, slow=2.5)),                    # ขั้น 4 เกินกรอบ 7 วันทำการ
        (25, Plan()),
        (38, Plan(stop_at=5)),
        (47, Plan(stop_at=5, extend_subc=True)),                             # อนุฯ ขยายเวลาแล้ว
        (56, Plan(slow=1.4)),
        (35, Plan(route="DIRECT", route_reason="เรื่องเร่งด่วนตามความเห็นประธานอนุกรรมการฯ", stop_at=7)),
        (74, Plan(stop_at=7, slow=1.3)),
        (86, Plan(stop_at=7, slow=1.6)),                                     # ใกล้ครบ 90 วัน
        (97, Plan(stop_at=7, slow=1.8)),                                     # เกิน 90 วัน
        (9, Plan(screening="REJECTED", route="SUBCOMMITTEE")),               # ยื่นพ้นกำหนด → เสนอไม่รับ
        (47, Plan(stop_at=6)),                                               # รอจัดทำวาระ กกพ.
        (66, Plan(stop_at=8)),                                               # กกพ. มีมติแล้ว รอแจ้งผล
        (74, Plan(stop_at=9)),                                               # แจ้งผลแล้ว รอปิดเรื่อง
    ]
    for i, (days_ago, plan) in enumerate(active):
        cls = "restricted" if i in (5, 11) else "internal"
        plan.officer = "lgl2" if cls == "restricted" else ("lgl1" if i % 2 == 0 else "lgl2")
        if plan.screening == "REJECTED":
            plan.result = "ไม่รับอุทธรณ์ไว้พิจารณา (ยื่นพ้นกำหนด 30 วัน)"
        else:
            plan.result = rng.choice(APPEAL_RESULTS)
        one_case(today - timedelta(days=days_ago), TEMPLATES[(i + 2) % len(TEMPLATES)], plan, cls,
                 late=plan.screening == "REJECTED")

    # เรื่องร้องเรียน (ระเบียบจัดหาไฟฟ้า/ประกาศรับซื้อ และข้อพิพาทตามสัญญาซื้อขายไฟฟ้า)
    for i, (days_ago, plan) in enumerate([(400, Plan()), (180, Plan(route="DIRECT", route_reason="มีแนวคำวินิจฉัยแล้ว")),
                                          (33, Plan(stop_at=5)), (8, Plan(stop_at=4)),
                                          (20, Plan(screening="REJECTED", route="DIRECT",
                                                    route_reason="ไม่อยู่ในอำนาจ กกพ. ตามสัญญา"))]):
        plan.result = ("ไม่รับเรื่องร้องเรียนไว้พิจารณา (ไม่อยู่ในอำนาจ กกพ.)" if plan.screening == "REJECTED"
                       else rng.choice(COMPLAINT_RESULTS))
        plan.officer = "lgl1" if i % 2 == 0 else "lgl2"
        one_case(today - timedelta(days=days_ago), COMPLAINTS[i % len(COMPLAINTS)], plan, "internal", "COMPLAINT")
    return made, extra


def generate_cases(conn: Connection, appeals: list[dict], windows, rng: random.Random) -> None:
    # คดีที่สืบเนื่องจากเรื่องอุทธรณ์ที่ยกหรือไม่รับคำอุทธรณ์
    sources = [a for a in appeals if a["case_type"] == "APPEAL" and a["status"] == "CLOSED" and a["result"] and
               ("ยก" in a["result"] or "ไม่รับ" in a["result"] or "แก้คำสั่ง" in a["result"]) and a["appeal_no"] != "อธ. 09/2565"]
    for i, a in enumerate(sources[:16]):
        filed = a["received"] + timedelta(days=rng.randint(120, 200))
        final = i % 3 == 0 and filed.year <= 2023
        black = f"{'คม' if i % 2 else 'พ'}. {rng.randint(100, 980)}/{be(filed)}"
        judgment = filed + timedelta(days=rng.randint(500, 900)) if final else None
        red = f"อ. {rng.randint(200, 1500)}/{be(judgment)}" if final else None
        level = "ศาลปกครองสูงสุด" if final else "ศาลปกครองชั้นต้น"
        procs = [{"d": filed, "t": "ยื่นคำฟ้องต่อศาลปกครองชั้นต้น", "x": f"คดีหมายเลขดำที่ {black}"},
                 {"d": filed + timedelta(days=60), "t": "สำนักงาน กกพ. ยื่นคำให้การ",
                  "x": "ชี้แจงข้อเท็จจริงและฐานอำนาจตามตัวบทที่ใช้บังคับ ณ วันเกิดเหตุ"}]
        if final:
            procs.append({"d": judgment, "t": "ศาลปกครองสูงสุดมีคำพิพากษาถึงที่สุด",
                          "x": f"คดีหมายเลขแดงที่ {red} พิพากษายืนตามคำวินิจฉัย กกพ."})
        result = "พิพากษายืนตามคำวินิจฉัย กกพ. (ยกฟ้อง)" if final else "อยู่ระหว่างการพิจารณาของศาลปกครองชั้นต้น"
        insert_case(conn, {
            "appeal_id": a["appeal_id"], "black": black, "red": red, "level": level, "court_name": level,
            "category": "คดีปกครอง", "subject": "คดีพิพาทสืบเนื่องจาก" + a["subject"].replace("อุทธรณ์", "", 1),
            "summary": f"ผู้ฟ้องคดีนำคดีมาฟ้องขอให้เพิกถอนคำวินิจฉัยอุทธรณ์ที่ {a['appeal_no']} ซึ่ง{a['result'].split(' (')[0]} "
                       f"ประเด็นข้อพิพาทเกี่ยวกับ" + "และ".join(LABEL[c["eli"]].split(" ของ")[0] for c in a["citations"]),
            "party_code": a["party_code"], "party_label": "ผู้ฟ้องคดี (" + a["party_label"] + ")",
            "defendant": "คณะกรรมการกำกับกิจการพลังงาน (ผู้ถูกฟ้องคดี)", "sector": a["sector"],
            "incident": a["incident"], "filed": filed, "judgment": judgment, "status": "FINAL" if final else "PENDING",
            "stage": "คดีถึงที่สุด" if final else "อยู่ระหว่างแสวงหาข้อเท็จจริง", "result": result, "cls": a["cls"],
            "owners": a["owners"], "proceedings": procs,
            "documents": [{"doc_no": None, "doc_type": "คำให้การของผู้ถูกฟ้องคดี (กกพ.)",
                           "doc_date": filed + timedelta(days=60), "pages": rng.randint(10, 30),
                           "excerpt": "ผู้ถูกฟ้องคดีขอเรียนต่อศาลว่า คำวินิจฉัยอุทธรณ์ออกโดยอาศัยอำนาจตาม"
                                      + "และ".join(f"{LABEL[c['eli']].split(' ของ')[0]} (รุ่นที่ {c['version']})" for c in a["citations"])
                                      + " ที่ใช้บังคับ ณ วันเกิดเหตุ จึงชอบด้วยกฎหมาย"}],
            "citations": a["citations"],
        })


def run(conn: Connection, today: date | None = None) -> dict:
    today = today or date.today()
    rng = random.Random(42)
    reset_schema(conn)
    for sd in STEPS:
        conn.execute(text("""INSERT INTO workflow_step_def (step_no, code, name_th, optional, anchors, limit_days, day_type,
                                                         limit_text, basis)
                             VALUES (:n, :c, :name, :opt, :anch, :days, :dt, :lt, :b)"""),
                     {"n": sd.step_no, "c": sd.code, "name": sd.name_th, "opt": sd.optional, "anch": list(sd.anchors),
                      "days": sd.limit_days, "dt": sd.day_type, "lt": sd.limit_text, "b": sd.basis})
    data = json.loads((HERE / "prototype_data.json").read_text(encoding="utf-8"))
    windows = load_laws(conn, data["instruments"])
    proto_ids = load_prototype(conn, data, windows, today, rng)
    generated, extra = generate(conn, windows, today, rng)
    load_prototype_cases(conn, data, proto_ids, extra)
    generate_cases(conn, generated, windows, rng)
    n_law = reindex_laws(conn)
    n_case = reindex_cases(conn)
    counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
              for t in ("provision_version", "appeal", "court_case", "citation_link")}
    return {**counts, "law_chunks": n_law, "case_chunks": n_case}


if __name__ == "__main__":
    with get_engine().begin() as c:
        print(run(c))
