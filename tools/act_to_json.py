"""แปลงข้อความ พ.ร.บ. (ผลจาก tools/pdf_lines.py) เป็น JSON สำหรับ app.seed.load_laws

    python tools/pdf_lines.py <act.pdf> /tmp/lines.tsv        # ต้องติดตั้ง pymupdf
    python tools/act_to_json.py /tmp/lines.tsv backend/app/seed/laws/act-2550-energy-industry.json

โครงสร้างที่รู้จัก: หมวด / ส่วนที่ / บทเฉพาะกาล, มาตรา (รวม "มาตรา ๒๘/๑"), ย่อหน้า (บรรทัดย่อหน้าเยื้อง)
มาตราที่มีเชิงอรรถ "แก้ไขเพิ่มเติมโดย ..." จะได้ 2 รุ่น: รุ่นเดิม (ไม่มีถ้อยคำในไฟล์ฉบับรวม) + รุ่นปัจจุบัน
"""
import json
import re
import sys
from datetime import date, timedelta

TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
ELI = "eli/th/act/2550/energy-industry"
EFFECTIVE = "2007-12-11"  # ม.2 ใช้บังคับวันถัดจากวันประกาศ (ราชกิจจานุเบกษา ๑๐ ธ.ค. ๒๕๕๐)
MONTHS = {m: i + 1 for i, m in enumerate("มกราคม กุมภาพันธ์ มีนาคม เมษายน พฤษภาคม มิถุนายน กรกฎาคม สิงหาคม กันยายน ตุลาคม พฤศจิกายน ธันวาคม".split())}

SEC = re.compile(r"^มาตรา ?([๐-๙]+(?:/[๐-๙]+)?) ?(.*)$")
HEAD = re.compile(r"^(หมวด [๐-๙]+|ส่วนที่ [๐-๙]+|บทเฉพาะกาล)$")


def join(prev: str, nxt: str) -> str:
    # บรรทัดภาษาไทยตัดตรงรอยคำ: ต่อโดยไม่เว้นวรรค ยกเว้นหลังวงเล็บ/ตัวเลข/เครื่องหมายคำพูด
    if not prev:
        return nxt
    if re.search(r"[)\]”\"0-9๐-๙A-Za-z.]$", prev) or nxt.startswith(("(", "“", "พ.ศ.")):
        return prev + " " + nxt
    return prev + nxt


def gazette_date(s: str) -> date | None:
    m = re.search(r"([๐-๙]+) (\S+) ([๐-๙]{4})", s)
    if not m or m.group(2) not in MONTHS:
        return None
    return date(int(m.group(3).translate(TH)) - 543, MONTHS[m.group(2)], int(m.group(1).translate(TH)))


def main(src: str, out: str) -> None:
    rows = [l.split("\t", 3) for l in open(src, encoding="utf-8").read().splitlines()]
    rows = [(int(p), int(x), float(sz), t) for p, x, sz, t in rows if t.strip()]
    body = [r for r in rows if r[2] >= 15.5 and not re.match(r"^- [๐-๙]+ -", r[3]) and r[3] != "สำนักงานคณะกรรมการกฤษฎีกา"]
    notes = [r for r in rows if r[2] < 15.5 and r[2] >= 13.5]

    # เชิงอรรถการแก้ไข: "มาตรา ๑๔๑ แก้ไขเพิ่มเติมโดย<ชื่อกฎหมาย>" (อาจต่อบรรทัด) + ราชกิจจานุเบกษาของกฎหมายนั้น
    note_text = ""
    for _, _, _, t in notes:
        note_text = join(note_text, t) if not re.match(r"^[๐-๙]+ ", t) else note_text + " " + t
    amended = {}
    for m in re.finditer(r"มาตรา ([๐-๙/]+) (?:แก้ไขเพิ่มเติม|เพิ่มเติม)โดย(.+?พ\.ศ\. [๐-๙]{4})", note_text):
        amended[m.group(1)] = m.group(2).strip()
    gazettes = re.findall(r"ราชกิจจานุเบกษา เล่ม ([๐-๙]+)/ตอนที่ ([๐-๙]+ ?\S*)/หน้า ([๐-๙]+)/([๐-๙]+ \S+ [๐-๙]{4})", note_text)

    provisions, part, chapter, section = [], None, None, None
    pending_head = None
    i = 0
    started = False
    while i < len(body):
        p, x, sz, t = body[i]
        if t.startswith("ผู้รับสนองพระบรมราชโองการ"):
            break
        if HEAD.match(t):
            title = ""
            if not t.startswith("บทเฉพาะกาล") and i + 1 < len(body) and not SEC.match(body[i + 1][3]) and not HEAD.match(body[i + 1][3]):
                title = body[i + 1][3]
                i += 1
            if t.startswith("หมวด") or t == "บทเฉพาะกาล":
                chapter, part = f"{t} {title}".strip(), None
            else:
                part = f"{t} {title}".strip()
            i += 1
            continue
        m = SEC.match(t)
        if m and x >= 140:
            started = True
            num = m.group(1)
            section = {"num": num, "paras": [m.group(2)], "part": " · ".join(x for x in [chapter, part] if x), "page": p}
            provisions.append(section)
        elif started and section:
            if x >= 140:
                section["paras"].append(t)
            else:
                section["paras"][-1] = join(section["paras"][-1], t)
        i += 1

    out_provs = []
    for s in provisions:
        n_ar = s["num"].translate(TH)
        pid = f"{ELI}/sec-{n_ar.replace('/', '-')}"
        text = "\n".join(x for x in s["paras"] if x)
        text = re.sub(r"มาตรา(?=[๐-๙])", "มาตรา ", text)
        base = {"amendingInstrument": "ตัวบทตั้งต้น (ราชกิจจานุเบกษา เล่ม ๑๒๔ ตอนที่ ๘๙ ก วันที่ ๑๐ ธันวาคม ๒๕๕๐)",
                "summary": s["part"] or None, "relations": []}
        if s["num"] in amended:
            act = amended[s["num"]]
            g = next((g for g in gazettes if g[0] != "๑๒๔"), None)
            pub = gazette_date(g[3]) if g else None
            vf = (pub + timedelta(days=1)).isoformat() if pub else None
            versions = [
                {**base, "version": 1, "validFrom": EFFECTIVE, "validTo": (pub.isoformat() if pub else None),
                 "text": f"[ถ้อยคำเดิมของมาตรา {s['num']} ก่อนแก้ไขโดย{act} ไม่มีในไฟล์ฉบับรวม — ตรวจจากราชกิจจานุเบกษา เล่ม ๑๒๔ ตอนที่ ๘๙ ก]",
                 "relations": [{"type": "ถูกแก้ไขโดย", "target": act}]},
                {**base, "version": 2, "validFrom": vf, "validTo": None, "text": text,
                 "amendingInstrument": f"{act}" + (f" (ราชกิจจานุเบกษา เล่ม {g[0]} ตอนที่ {g[1]} วันที่ {g[3]})" if g else ""),
                 "relations": [{"type": "แก้ไขเพิ่มเติมจาก", "target": act}]},
            ]
        else:
            versions = [{**base, "version": 1, "validFrom": EFFECTIVE, "validTo": None, "text": text}]
        out_provs.append({"id": pid, "number": f"มาตรา {n_ar}", "part": s["part"] or None, "versions": versions,
                          "sourcePage": s["page"]})

    data = [{
        "id": ELI, "type": "พระราชบัญญัติ", "title": "พระราชบัญญัติการประกอบกิจการพลังงาน พ.ศ. 2550",
        "code": "พ.ร.บ. 2550", "issuer": "สภานิติบัญญัติแห่งชาติ", "category": "กิจการพลังงานทั่วไป",
        "publishedDate": "2007-12-10", "effectiveDate": EFFECTIVE, "isDemo": False,
        "source": "ฉบับรวมของสำนักงานคณะกรรมการกฤษฎีกา (ปรับปรุงถึง พ.ร.บ. แก้ไขเพิ่มเติม พ.ศ. ๒๕๖๐)",
        "provisions": out_provs,
    }]
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    nums = [int(s["num"].split("/")[0].translate(TH)) for s in provisions]
    missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
    print(f"มาตรา {len(provisions)} (สูงสุด {max(nums)}), ขาดเลข: {missing or '-'}, แก้ไขเพิ่มเติม: {list(amended)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
