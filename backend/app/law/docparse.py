"""แปลงข้อความกฎหมาย (Word / PDF มีชั้นข้อความ / ผล OCR) → ตราสารรูปแบบเดียวกับ seed/laws/*.json

ใช้ร่วมกันโดย tools/legal_import/convert_bulk.py (นำเข้าเป็นชุดจากฝ่ายกฎหมาย) และหน้านำเข้าเอกสารบนเว็บ (law/uploads.py)
"""
import re
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

TD = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
MONTHS = {m: i for i, m in enumerate(["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม",
                                      "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"], 1)}
DATE_RE = re.compile(r"([๐-๙\d]{1,2})\s*(" + "|".join(MONTHS) + r")\s*(?:พ\.ศ\.\s*)?([๐-๙\d]{4})")
GAZ_RE = re.compile(r"เล่ม\s*([๐-๙\d]+)\s*ตอน(พิเศษ)?\s*(?:ที่\s*)?([๐-๙\d]+)\s*([กขคง])")
HEADER_RE = re.compile(r"^\s*(?:หนา\s*[๐-๙\d]+|-\s*[๐-๙\d]+\s*-|[๐-๙\d]+)\s*$|เลม\s*[๐-๙\d]+\s*ตอน.*ราชกจจานเบกษา|^\s*ราชกจจานเบกษา\s*$|^\s*เลม\s*[๐-๙\d]+\s*ตอน|^\s*หนา\s*[๐-๙\d]+\s+เลม")
SIGN_RE = re.compile(r"^(?:ประกาศ|ให้ไว้|สั่ง)\s*ณ\s*วันที่")
PART_RE = re.compile(r"^(หมวด\s*[๐-๙\d]+.*|บทเฉพาะกาล|ส่วนที่\s*[๐-๙\d]+.*)$")
NONINIT = "ะัาำิีึืฺุูๅ็่้๊๋์ํ๎"


def th2ar(s: str) -> str:
    return s.translate(TD)


def iso(m) -> str | None:
    try:
        d, mon, y = int(th2ar(m.group(1))), MONTHS[m.group(2)], int(th2ar(m.group(3))) - 543
        return date(y, mon, d).isoformat()
    except (ValueError, KeyError):
        return None


# ฟอนต์ไทยรุ่นเก่าใน PDF เก็บสระบน/วรรณยุกต์ที่เลื่อนตำแหน่งเป็นอักขระ Private Use (U+F700–F71A)
PUA = dict(zip(range(0xF700, 0xF71B), "ฐ\u0e34\u0e35\u0e36\u0e37\u0e48\u0e49\u0e4a\u0e4b\u0e4c\u0e48\u0e49\u0e4a\u0e4b\u0e4c"
                                        "ญ\u0e31\u0e4d\u0e47\u0e48\u0e49\u0e4a\u0e4b\u0e4c\u0e38\u0e39\u0e3a"))


# สัดส่วนวรรณยุกต์ ่ ้ ต่ออักษรไทย: ข้อความจาก PDF ที่ฟอนต์เพี้ยน/ไม่มีชั้นข้อความต่ำกว่าเกณฑ์นี้ → ต้อง OCR
MIN_TONE_RATIO = 0.06
# ข้อความแทนหน้าที่ OCR ข้ามเพราะเรนเดอร์ภาพไม่สำเร็จ (ocr.py ใส่, build_instrument ใส่หมายเหตุให้ตรวจ)
SKIPPED_PAGE = "[หน้านี้เรนเดอร์ไม่สำเร็จ — ต้องตรวจจากต้นฉบับ]"


def tone_ratio(t: str) -> float:
    th = len(re.findall(r"[\u0e01-\u0e2e]", t))
    return len(re.findall(r"[\u0e48\u0e49]", t)) / th if th else 0.0


def fix_chars(s: str) -> str:
    s = unicodedata.normalize("NFC", s.translate(PUA)).replace(" ", " ").replace("​", "")
    s = re.sub(r"ํ\s*า", "ำ", s)                       # ํ า → ำ
    s = re.sub(r"\s+([" + NONINIT + "])", r"\1", s)                    # วรรณยุกต์/สระที่หลุดไปหลังช่องว่าง
    s = re.sub(r"([เแโใไ])\s+(?=[ก-ฮ])", r"\1", s)                     # สระหน้าแยกจากพยัญชนะ
    s = re.sub(r"([\u0e32\u0e33])([\u0e48-\u0e4b])", r"\2\1", s)          # "หนา้" → "หน้า" (วรรณยุกต์อยู่หลังสระอา)
    s = re.sub(r"([ก-ฮ])([ก-ฮ])\u0e31([\u0e34-\u0e37])", "\\1\u0e31\\2\\3", s)  # "ปฏิบตัิ" → "ปฏิบัติ"
    return s


# PDF บางฟอนต์ทำนิคหิตหาย: "ำ" กลายเป็น "า" — แก้เฉพาะเอกสารที่มีอาการ (พบ "กากับ"/"กาหนด") ด้วยคำที่พบบ่อยในตัวบท
AM_WORDS = ["กากับ", "กาหนด", "ดาเนิน", "จัดทา", "สานัก", "สาคัญ", "ทาให้", "ทาการ", "ต่ากว่า", "จาเป็น", "จานวน",
            "คาขอ", "คาสั่ง", "คาวินิจฉัย", "คาร้อง", "คาว่า", "คานวณ", "คาตอบ", "ประจาปี", "ประจา", "นาไป", "นามา",
            "นาส่ง", "นาเสนอ", "อานาจ", "ชานาญ", "กาไร", "ลาดับ", "ตาแหน่ง", "สารวจ", "สารอง", "ดารง", "บารุง",
            "จาหน่าย", "ซ้าซ้อน", "กาลัง", "ทาง่าย", "สาเนา", "สาเร็จ", "ทาหน้าที่", "จากัด", "อาเภอ", "กาเนิด",
            "นาเข้า", "ปฏิบัติตามคาสั่ง", "ข้อกาหนด", "ค้าประกัน", "คาแนะนา", "แนะนา", "ทาสัญญา", "ทาความ", "ทารายงาน"]
AM_FIX = sorted(((w, w.replace("า", "ำ", 1) if not w.startswith(("ต่า", "ค้า", "ซ้า")) else w.replace("า", "ำ", 1)) for w in AM_WORDS),
                key=lambda kv: -len(kv[0]))


def fix_lost_am(text: str) -> str:
    if "กากับ" not in text and "กาหนด" not in text:
        return text
    for bad, good in AM_FIX:
        text = text.replace(bad, good)
    return text


def bare(s: str) -> str:
    """ตัดสระบน/ล่างและวรรณยุกต์ — ใช้ตรวจหัวกระดาษที่ลำดับวรรณยุกต์เพี้ยน"""
    return re.sub("[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]", "", s)


def unjustify(line: str) -> str:
    """บรรทัดที่ PDF กระจายตัวอักษรให้เต็มบรรทัด ("เ ป็ น การ") → ต่อคำ"""
    toks = line.split(" ")
    thai = [t for t in toks if re.search(r"[ก-ฮ]", t)]
    if len(thai) >= 6 and sum(len(re.sub(r"[^ก-ฮ]", "", t)) <= 1 for t in thai) / len(thai) > 0.25:
        line = re.sub(r"(?<=[ก-๎]) (?=[ก-๎])", "", line)
    return line



def pdf_text(path: str) -> str:
    """ชั้นข้อความของ PDF โดยรักษาตำแหน่งบรรทัด (pdftotext -layout)"""
    return subprocess.run(["pdftotext", "-q", "-layout", path, "-"], capture_output=True, text=True, timeout=300).stdout


def pdf_paragraphs(path: str) -> list[str]:
    return layout_paragraphs(pdf_text(path))


def layout_paragraphs(txt: str) -> list[str]:
    """ข้อความที่รักษาตำแหน่งบรรทัด (pdftotext -layout หรือ ocr.py) → ย่อหน้า: บรรทัดเยื้อง/ขึ้นต้นด้วยข้อ มาตรา (๑) = ย่อหน้าใหม่"""
    paras: list[str] = []
    for raw in txt.replace("\f", "\n").splitlines():
        if not raw.strip() or HEADER_RE.search(bare(fix_chars(raw))):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = unjustify(fix_chars(re.sub(r"[ \t]{2,}|\t", " ", raw.strip())))
        if re.fullmatch(r"[_\-–—.\s]+", line):
            continue
        starts = (indent >= 4 or not paras or re.match(r"^(ข้อ|มาตรา)\s*[๐-๙\d]", line) or re.match(r"^\(?[๐-๙\d]+\)", line)
                  or SIGN_RE.match(line) or PART_RE.match(line))
        if starts:
            paras.append(line)
        else:
            prev = paras[-1]
            sep = "" if re.search(r"[ก-๎]$", prev) and re.match(r"[ก-๎]", line) else " "
            paras[-1] = prev + sep + line
    return paras


def docx_paragraphs(path: str) -> list[str]:
    import docx
    out = []
    for p in docx.Document(path).paragraphs:
        for line in p.text.split("\n"):
            line = fix_chars(re.sub(r"[ \t]{2,}|\t", " ", line.strip()))
            if line and not re.fullmatch(r"[_\-–—.\s]+", line):
                out.append(line)
    return out


def doc_paragraphs(path: str, tmp: Path | None = None) -> list[str]:
    """.doc → .docx ด้วย LibreOffice (ต้องมี soffice) — ไม่มี → คืนรายการว่าง"""
    with tempfile.TemporaryDirectory() as td:
        out = Path(tmp or td)
        out.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["soffice", "--headless", "--convert-to", "docx", "--outdir", str(out), path],
                           capture_output=True, timeout=180)
        except FileNotFoundError:
            return []
        f = out / (Path(path).stem + ".docx")
        return docx_paragraphs(str(f)) if f.exists() else []




THAI_RE = re.compile(r"[ก-๙]")


def issuer_of(title: str) -> tuple[str, str]:
    if re.search(r"สำนักงานคณะกรรมการกำกับกิจการพลังงาน|สำนักงาน กกพ|สกพ", title[:60]):
        return "oerc", "สำนักงานคณะกรรมการกำกับกิจการพลังงาน"
    if "กระทรวงพลังงาน" in title or "รัฐมนตรี" in title[:40]:
        return "moen", "กระทรวงพลังงาน"
    if "นโยบายพลังงานแห่งชาติ" in title:
        return "nepc", "คณะกรรมการนโยบายพลังงานแห่งชาติ"
    if title.startswith("พระราชกฤษฎีกา"):
        return "rd", "พระมหากษัตริย์ (พระราชกฤษฎีกา)"
    return "erc", "คณะกรรมการกำกับกิจการพลังงาน"


TYPE_SEG = {"ระเบียบ": "reg", "ประกาศ": "ann", "พระราชกฤษฎีกา": "rd", "ข้อบังคับ": "rule", "คำสั่ง": "order"}
# หมวดหมู่ใช้จัดกลุ่มเพื่อค้นหาเท่านั้น (ทีมพัฒนากำหนด รอฝ่ายกฎหมายรับรอง — ปรับได้โดยไม่กระทบชั้นข้อมูล)
# ใช้หมวดแรกที่พบคำในชื่อเรื่อง ตามลำดับในรายการ
CATEGORIES = [("การจัดหาไฟฟ้า", r"รับซื้อไฟฟ้า|จัดหาไฟฟ้า|Feed|FiT|ผู้ผลิตไฟฟ้า"),
              ("สิ่งแวดล้อมและประมวลหลักการปฏิบัติ", r"ประมวลหลักการปฏิบัติ|สิ่งแวดล้อม|Code of Practice|CoP"),
              ("กิจการก๊าซธรรมชาติ", r"ก๊าซ|LNG"),
              ("อัตราค่าบริการ", r"อัตราค่า|ค่าบริการ|ค่าไฟฟ้า|ค่าธรรมเนียม|Ft"),
              ("การอนุญาต", r"ใบอนุญาต|การอนุญาต"),
              ("มาตรฐานและคุณภาพบริการ", r"มาตรฐาน|คุณภาพการให้บริการ|ความปลอดภัย|ระบบโครงข่าย|Grid Code"),
              ("เรื่องร้องเรียนและข้อพิพาท", r"ร้องเรียน|ข้อพิพาท|อุทธรณ์|เปรียบเทียบปรับ|บังคับทางปกครอง"),
              ("กองทุนพัฒนาไฟฟ้า", r"กองทุน")]
GENERAL_CATEGORY = "กิจการพลังงานทั่วไป"
INTERNAL_CATEGORY = "การบริหารองค์กร"   # หมวดของระเบียบบริหารงานภายในสำนักงาน

# ---- ชั้นข้อมูล (ภายใน/สาธารณะ) — กฎแยกจากหมวดหมู่โดยตั้งใจ ----
# ชื่อเรื่องมีคำเรื่องบริหารงานภายใน และไม่มีคำของเรื่องกิจการพลังงาน → ระเบียบบริหารงานภายใน (เห็นเฉพาะเจ้าหน้าที่)
# เดิมใช้ "หมวดแรกที่พบ" ตัดสินชั้นข้อมูลไปด้วย ทำให้การแก้หมวดหมู่เปลี่ยนชั้นข้อมูลได้โดยไม่ตั้งใจ
# กฎนี้ให้ผลเท่าเดิมทุกฉบับ (tests/test_classification_rule.py ตรึงไว้) — เปลี่ยนกฎนี้ต้องให้ฝ่ายกฎหมายรับรอง
INTERNAL_RX = re.compile(r"สรรหา|คัดเลือก|เบี้ยประชุม|ค่าตอบแทน|การเงิน|บัญชี|เลขาธิการ|ตรวจสอบภายใน|คณะกรรมการตรวจสอบ|"
                         r"พนักงาน|ลูกจ้าง|การประชุม|สำนักงาน|เบิกจ่าย|พัสดุ|รายได้แผ่นดิน|กฎบัตร|ใช้รถ", re.I)
# คำของเรื่องกิจการพลังงาน — สำเนาคงที่ของคำในหมวดหมู่ ณ วันที่แยกกฎ (ห้ามอ้าง CATEGORIES เพื่อให้แก้หมวดได้อิสระ)
ENERGY_RX = re.compile(r"รับซื้อไฟฟ้า|จัดหาไฟฟ้า|Feed|FiT|ผู้ผลิตไฟฟ้า|ประมวลหลักการปฏิบัติ|สิ่งแวดล้อม|Code of Practice|CoP|"
                       r"ก๊าซ|LNG|อัตราค่า|ค่าบริการ|ค่าไฟฟ้า|ค่าธรรมเนียม|Ft|ใบอนุญาต|การอนุญาต|"
                       r"มาตรฐาน|คุณภาพการให้บริการ|ความปลอดภัย|ระบบโครงข่าย|Grid Code|"
                       r"ร้องเรียน|ข้อพิพาท|อุทธรณ์|เปรียบเทียบปรับ|บังคับทางปกครอง|กองทุน", re.I)


def internal_keyword(title: str | None) -> str | None:
    """คำเรื่องบริหารงานภายในที่พบในชื่อเรื่อง (ใช้เตือนผู้ตรวจ แม้ฉบับนั้นจะเป็นสาธารณะ)"""
    m = INTERNAL_RX.search(title or "")
    return m.group(0) if m else None


def is_internal_title(*titles: str | None) -> bool:
    """ชื่อใดชื่อหนึ่งเป็นเรื่องบริหารงานภายใน → ภายใน (ตรวจทั้งชื่อในทะเบียนและชื่อจากตัวบท
    เพื่อไม่ให้ชื่อย่อในทะเบียน เช่น "ลกพ." ทำให้ฉบับภายในหลุดเป็นสาธารณะ)"""
    return any(t and INTERNAL_RX.search(t) and not ENERGY_RX.search(t) for t in titles)


def category_of(title: str) -> str:
    for name, rx in CATEGORIES:
        if re.search(rx, title, re.I):
            return name
    return INTERNAL_CATEGORY if is_internal_title(title) else GENERAL_CATEGORY


OCR_CLAUSE = re.compile(r"^(?:ข้อ|มาตรา)\s*([^\s.()]{1,3})\s+(.*)$")  # ไม่รับ "(๑)" (อนุข้อที่ขึ้นบรรทัดใหม่)


def parse(paras: list[str], kind: str, reg_title: str, ocr: bool = False, renumbered: list | None = None,
          gaps: list | None = None):
    """ocr=True: เลขข้อไม่ต่อจากข้อก่อนหน้า → ดูเลขของหัวข้อถัดไปเพื่อแยกสองกรณี
      - หัวข้อหายไป (OCR อ่านหัวข้อไม่ออก เช่น ข้อ ๓ รวมอยู่กับข้อ ๒) → ใช้เลขที่อ่านได้ บันทึกเลขที่หายใน gaps
      - เลขอ่านผิด (๔↔๕↔๖, "of") → กำหนดเป็นข้อถัดไปตามลำดับ บันทึกใน renumbered
    ทั้งสองรายการใช้แจ้งในประเด็นรอตรวจ"""
    # ชื่อเรื่อง: ตั้งแต่ต้นจนบรรทัด "พ.ศ. ๒๕xx" (ภายใน 8 ย่อหน้าแรก)
    title, start = None, 0
    for i, p in enumerate(paras[:8]):
        if re.search(r"พ\.\s*ศ\.\s*[๐-๙\d]{4}\s*$", p):
            title, start = " ".join(paras[: i + 1]), i + 1
            break
    if title and len(title) > 300:
        title, start = None, 0
    cls = re.compile(rf"^{kind}\s*([๐-๙\d]+(?:/[๐-๙\d]+)?)\s*(.*)$")
    pre, provs, signed, tail, annex = [], [], None, 0, []
    depth, last, part = 0, None, None
    i = start
    while i < len(paras):
        p = paras[i]
        nxt = paras[i + 1] if i + 1 < len(paras) else ""
        if depth == 0 and SIGN_RE.match(p) and (provs or pre) and len(p) < 90 and not re.match(r"^\(?[๐-๙\d]+\)", nxt):
            signed = " ".join(paras[i: i + 3])
            annex = paras[i + 3:]
            tail = len(annex)
            break
        # OCR อ่านเครื่องหมายคำพูดปิด (”) ผิดบ่อย จนนับความลึกค้าง — โหมด OCR ไม่ใช้ความลึกกั้นการขึ้นข้อใหม่
        m = cls.match(p) if depth == 0 or ocr else None
        if m:
            no = th2ar(m.group(1))
            major = int(no.split("/")[0])
            ok = last is None and major in (1, 2) or last is not None and (
                (major == last[0] + 1 and "/" not in no) or (major == last[0] and "/" in no) or ("/" in no and major == last[0]))
            if ok and not any(x["no"] == no for x in provs):
                provs.append({"no": no, "lines": [m.group(2)] if m.group(2) else [], "part": part})
                last = (major, no)
                depth = max(0, p.count("“") - p.count("”"))
                i += 1
                continue
        if m and not ok and ocr and last is not None and "/" not in no and not any(x["no"] == no for x in provs):
            # เลขไม่ต่อเนื่อง: หัวข้อหาย หรือเลขอ่านผิด? ดูเลขของหัวข้อถัดไป
            nxt_no = next((int(th2ar(mm.group(1)).split("/")[0]) for q in paras[i + 1: i + 60]
                           for mm in [cls.match(q)] if mm), None)
            gap_ok = last[0] + 1 < major <= last[0] + 3 and (nxt_no is None or nxt_no == major + 1)
            if gap_ok:
                if gaps is not None:
                    gaps.extend(str(k) for k in range(last[0] + 1, major))
                provs.append({"no": no, "lines": [m.group(2)] if m.group(2) else [], "part": part})
                last = (major, no)
                depth = max(0, p.count("“") - p.count("”"))
                i += 1
                continue
        g = OCR_CLAUSE.match(p) if ocr and last is not None and p.startswith(kind) else None
        if g and (not m or not ok):
            no = str(last[0] + 1)
            if renumbered is not None:
                renumbered.append(f"{g.group(1)}→{no}")
            provs.append({"no": no, "lines": [g.group(2)], "part": part})
            last = (last[0] + 1, no)
            depth = max(0, p.count("“") - p.count("”"))
            i += 1
            continue
        if depth == 0 and PART_RE.match(p) and len(p) < 120:
            part = p
        elif provs:
            provs[-1]["lines"].append(p)
        else:
            pre.append(p)
        depth = max(0, depth + p.count("“") - p.count("”"))
        i += 1
    return (title or reg_title), pre, provs, signed, tail, annex




def gazette_from_text(t: str) -> tuple[str | None, str | None]:
    """ข้อความหัวราชกิจจานุเบกษา (เล่ม/ตอน/วันที่) จาก 3,000 อักษรแรก → (แหล่งอ้างอิง, วันประกาศ ISO)"""
    t = t[:3000]
    g = GAZ_RE.search(t)
    if not g:
        return None, None
    d = DATE_RE.search(t[g.end(): g.end() + 300]) or DATE_RE.search(t)
    src = f"ราชกิจจานุเบกษา เล่ม {g.group(1)} ตอน{'พิเศษ ' if g.group(2) else 'ที่ '}{g.group(3)} {g.group(4)}"
    if d:
        src += f" วันที่ {d.group(0)}"
    return src, (iso(d) if d else None)


def kind_of(doc_type: str) -> str:
    return "มาตรา" if doc_type in ("พระราชกฤษฎีกา", "พระราชบัญญัติ") else "ข้อ"


def build_instrument(paras: list[str], *, doc_type: str, reg_title: str, key: str, effective_date: str | None,
                     gaz: str | None, pub: str | None, ocr: bool, ext: str, short: str,
                     used_eli: set | None = None, dup_suffix: str = "", source_default: str) -> tuple[dict | None, str | None, list[str]]:
    """ย่อหน้า → ตราสาร (dict รูปแบบ seed/laws/*.json ยังไม่มี sourceFiles) หรือ (None, เหตุผลที่แปลงไม่ได้, [])

    effective_date: วันใช้บังคับจากทะเบียน/ผู้นำเข้า (ไม่มี → วันประกาศราชกิจจาฯ → วันที่ลงนาม)
    key: ส่วนท้าย ELI เช่น g1-12-1 (ทะเบียน 1.12.1) หรือ u17 (อัปโหลดรายการที่ 17)
    """
    kind = kind_of(doc_type)
    renum: list[str] = []
    gaps: list[str] = []
    title, pre, provs, signed, tail, annex = parse(paras, kind, reg_title, ocr=ocr, renumbered=renum, gaps=gaps)
    sig_date = iso(DATE_RE.search(signed)) if signed and DATE_RE.search(signed) else None
    eff = effective_date or pub or sig_date
    warn = []
    if not effective_date:
        warn.append(f"วันใช้บังคับไม่มีในทะเบียน — ใช้ {'วันประกาศราชกิจจาฯ' if pub else 'วันที่ลงนาม' if sig_date else '-'}")
    if not eff:
        return None, "ไม่พบวันที่ใช้บังคับ", []
    if not provs:
        body = [p for p in paras[len(title.split(" ")) and 0:] if p not in title]
        provs = [{"no": None, "lines": pre or body, "part": None}]
        pre = []
        warn.append("ไม่พบการแบ่งเป็นข้อ — เก็บทั้งฉบับเป็นบทบัญญัติเดียว")
    seen_nos = [int(th2ar(m.group(1))) for p in paras for m in [re.match(rf"^{kind}\s*([๐-๙\d]+)\s", p)] if m]
    if provs and provs[0]["no"] and seen_nos:
        top = max(int(x["no"].split("/")[0]) for x in provs)
        if max(seen_nos) > top and max(seen_nos) - top <= 60:
            warn.append(f"พบเลข{kind}สูงสุด {max(seen_nos)} แต่แยกได้ถึง {kind} {top} — ตรวจการแบ่ง{kind}")
    annex_text = "\n".join(annex).strip()
    if len(annex_text) > 200:
        if len(annex_text) > 40000:
            annex_text = annex_text[:40000] + "\n…"
            warn.append("เอกสารแนบท้ายยาวเกิน 40,000 อักษร — นำเข้าเฉพาะส่วนต้น ดูฉบับเต็มในไฟล์ต้นฉบับ")
        provs.append({"no": "__annex__", "lines": [annex_text], "part": "เอกสารแนบท้าย"})
        warn.append("เอกสารแนบท้าย (ตาราง/แบบฟอร์ม) นำเข้าเป็นข้อความ — รูปแบบตารางอาจไม่ครบ")
    if gaps:
        warn.append(f"ไม่พบหัว{kind} {', '.join(gaps[:8])} ในข้อความ OCR — เนื้อความอาจรวมอยู่กับ{kind}ก่อนหน้า ตรวจกับต้นฉบับ")
    if renum:
        warn.append(f"เลข{kind}จาก OCR ไม่ต่อเนื่อง กำหนดตามลำดับ ({', '.join(renum[:6])}{' …' if len(renum) > 6 else ''}) — ตรวจกับต้นฉบับ")
    skipped = sum(1 for x in paras if SKIPPED_PAGE in x)
    if skipped:
        warn.append(f"OCR ข้าม {skipped} หน้าที่เรนเดอร์ภาพไม่สำเร็จ — ข้อความของหน้านั้นขาดหาย ตรวจกับต้นฉบับ")
    if ocr:
        warn.append("ถอดจากภาพสแกนด้วย OCR — ตรวจทานตัวเลข วันที่ และวรรณยุกต์กับต้นฉบับทุกข้อก่อนอ้างอิง")
    elif ext == ".pdf":
        warn.append("ถอดข้อความจาก PDF — ตรวจคำที่ตัดผิด")
    code_issuer, issuer = issuer_of(title)
    year = (re.findall(r"25\d\d", th2ar(title)) or [str(int(eff[:4]) + 543)])[-1]
    seg = TYPE_SEG.get(doc_type, "doc")
    eli = f"eli/th/{seg}/{code_issuer}/{year}/{key}"
    if used_eli is not None:
        if eli in used_eli:
            eli += dup_suffix
        used_eli.add(eli)
    part_of = lambda no: (f"{eli}/item-annex" if no == "__annex__" else  # noqa: E731
                          f"{eli}/{'sec' if kind == 'มาตรา' else 'clause'}-{no.replace('/', '-')}" if no else f"{eli}/item-1")
    provisions = []
    for p in provs:
        body = "\n".join(x for x in p["lines"] if x).strip()
        if not body:
            continue
        number = "แนบท้าย" if p["no"] == "__annex__" else f"{kind} {p['no']}" if p["no"] else "ทั้งฉบับ"
        provisions.append({"id": part_of(p["no"]), "number": number, "part": p["part"], "sourcePage": None,
                           "versions": [{"version": 1, "validFrom": eff, "validTo": None, "text": body,
                                         "amendingInstrument": f"ตัวบทตั้งต้น ({gaz or source_default})",
                                         "summary": None, "relations": []}]})
    if not provisions:
        return None, "แยกข้อความไม่ได้", []
    cat = category_of(title)
    internal = is_internal_title(title, reg_title)
    kw = internal_keyword(title) or internal_keyword(reg_title)
    if kw and not internal:   # ปล่อยเป็นสาธารณะเพราะเป็นเรื่องกิจการพลังงาน — ให้ผู้ตรวจยืนยัน
        warn.append(f"ชื่อเรื่องมีคำ \"{kw}\" ซึ่งมักเป็นระเบียบบริหารงานภายใน แต่ระบบจัดเป็นสาธารณะ — ตรวจชั้นข้อมูล")
    inst = {"id": eli, "type": doc_type, "title": re.sub(r"\s+", " ", title).strip(), "code": short,
            "issuer": issuer, "category": cat,
            **({"classification": "internal"} if internal else {}),
            "publishedDate": pub, "effectiveDate": eff,
            "isDemo": False, "source": gaz or source_default,
            "registerTitle": reg_title, "preamble": "\n".join(pre) or None, "signed": signed,
            "provisions": provisions, "importNotes": warn}
    return inst, None, warn
