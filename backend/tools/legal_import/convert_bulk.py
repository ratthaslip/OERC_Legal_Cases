"""แปลงไฟล์กฎหมายลำดับรองจากฝ่ายกฎหมาย (Word/PDF มีข้อความ) → seed/laws/legal-team/*.json

ขอบเขตชุดที่ 1: แถวทะเบียนที่ "ใช้บังคับ" มีไฟล์ข้อความใช้ได้ ยังไม่มีตัวบทในระบบ และมีเอกสารเดียวในแถว
ฉบับแก้ไขเพิ่มเติมนำเข้าเป็นตราสารแยก (ยังไม่ประกอบเข้าฉบับหลัก — ระบบแสดงคำเตือนที่ฉบับหลัก)
ผลลัพธ์อื่น: $LEGAL_IMPORT_WORK/convert_report.json (ข้าม/เตือนรายแถว)
"""
import json
import re
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path

import os

WORK = Path(os.environ.get("LEGAL_IMPORT_WORK", "work"))
INV = WORK / "inventory.json"
OUT = Path(__file__).resolve().parents[2] / "app" / "seed" / "laws" / "legal-team"
REPORT = WORK / "convert_report.json"
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


def pdf_paragraphs(path: str) -> list[str]:
    txt = subprocess.run(["pdftotext", "-q", "-layout", path, "-"], capture_output=True, text=True).stdout
    paras: list[str] = []
    for raw in txt.splitlines():
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


def doc_paragraphs(path: str) -> list[str]:
    tmp = WORK / "text" / "doc"
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["soffice", "--headless", "--convert-to", "docx", "--outdir", str(tmp), path], capture_output=True, timeout=180)
    f = tmp / (Path(path).stem + ".docx")
    return docx_paragraphs(str(f)) if f.exists() else []


def best_file(files: list[dict]) -> dict | None:
    for ext in (".docx", ".doc"):
        c = [f for f in files if f["ext"] == ext and f["thai_chars"] > 300]
        if c:
            return max(c, key=lambda f: f["thai_chars"])
    c = [f for f in files if f["ext"] == ".pdf" and not f["scanned"] and f["thai_chars"] > 300]
    return max(c, key=lambda f: f["thai_chars"]) if c else None


def paragraphs(f: dict) -> list[str]:
    ps = {".docx": docx_paragraphs, ".doc": doc_paragraphs}.get(f["ext"], pdf_paragraphs)(f["disk"])
    joined = fix_lost_am("\n".join(ps))
    return joined.split("\n")


def gazette(files: list[dict]) -> tuple[str | None, str | None]:
    for f in files:
        t = WORK / "text" / f"{f['sha256']}.txt".read_text(encoding="utf-8")[:3000]
        g = GAZ_RE.search(t)
        if g:
            d = DATE_RE.search(t[g.end(): g.end() + 300]) or DATE_RE.search(t)
            src = f"ราชกิจจานุเบกษา เล่ม {g.group(1)} ตอน{'พิเศษ ' if g.group(2) else 'ที่ '}{g.group(3)} {g.group(4)}"
            if d:
                src += f" วันที่ {d.group(0)}"
            return src, (iso(d) if d else None)
    return None, None


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
CATEGORIES = [("การจัดหาไฟฟ้า", r"รับซื้อไฟฟ้า|จัดหาไฟฟ้า|Feed|FiT|ผู้ผลิตไฟฟ้า"),
              ("สิ่งแวดล้อมและประมวลหลักการปฏิบัติ", r"ประมวลหลักการปฏิบัติ|สิ่งแวดล้อม|Code of Practice|CoP"),
              ("กิจการก๊าซธรรมชาติ", r"ก๊าซ|LNG"),
              ("อัตราค่าบริการ", r"อัตราค่า|ค่าบริการ|ค่าไฟฟ้า|ค่าธรรมเนียม|Ft"),
              ("การอนุญาต", r"ใบอนุญาต|การอนุญาต"),
              ("มาตรฐานและคุณภาพบริการ", r"มาตรฐาน|คุณภาพการให้บริการ|ความปลอดภัย|ระบบโครงข่าย|Grid Code"),
              ("เรื่องร้องเรียนและข้อพิพาท", r"ร้องเรียน|ข้อพิพาท|อุทธรณ์|เปรียบเทียบปรับ|บังคับทางปกครอง"),
              ("กองทุนพัฒนาไฟฟ้า", r"กองทุน"),
              ("การบริหารองค์กร", r"สรรหา|คัดเลือก|เบี้ยประชุม|ค่าตอบแทน|การเงิน|บัญชี|เลขาธิการ|ตรวจสอบภายใน|คณะกรรมการตรวจสอบ|"
                                  r"พนักงาน|ลูกจ้าง|การประชุม|สำนักงาน|เบิกจ่าย|พัสดุ|รายได้แผ่นดิน|กฎบัตร|ใช้รถ")]


def category_of(title: str) -> str:
    for name, rx in CATEGORIES:
        if re.search(rx, title, re.I):
            return name
    return "กิจการพลังงานทั่วไป"


def parse(paras: list[str], kind: str, reg_title: str):
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
        m = cls.match(p) if depth == 0 else None
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
        if depth == 0 and PART_RE.match(p) and len(p) < 120:
            part = p
        elif provs:
            provs[-1]["lines"].append(p)
        else:
            pre.append(p)
        depth = max(0, depth + p.count("“") - p.count("”"))
        i += 1
    return (title or reg_title), pre, provs, signed, tail, annex


def main() -> None:
    inv = json.loads(INV.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    rep = {"converted": [], "skipped": []}
    used_eli = set()
    done_sha: dict[str, str] = {}
    for r in sorted(inv["rows"], key=lambda r: r["sheet_row"]):
        why = None
        if r["status"] != "IN_FORCE":
            why = "ยกเลิกแล้ว (ชุดถัดไป)"
        elif r["instrument_eli"]:
            why = "มีตัวบทในระบบแล้ว"
        elif not r["files"]:
            why = "ไม่มีไฟล์"
        elif len({re.sub(r"^\d+\s*\.\s*", "", f["name"].rsplit(".", 1)[0]) for f in r["files"]}) > 1:
            why = "มีมากกว่า 1 เอกสารในแถว — รอตรวจการจับคู่"
        f = best_file(r["files"]) if not why else None
        if not why and not f:
            why = "มีแต่ PDF สแกน (รอ OCR)"
        if why:
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": why, "title": r["title_th"][:90]})
            continue
        if f["sha256"] in done_sha:  # ทะเบียนหลายแถวชี้ไฟล์เดียวกัน → ตราสารเดียว (ผูกแถวอื่นด้วยชื่อเรื่อง)
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": f"ไฟล์เดียวกับ {done_sha[f['sha256']]}",
                                   "title": r["title_th"][:90]})
            continue
        done_sha[f["sha256"]] = f"ทะเบียน {r['reg_key']}"
        kind = "มาตรา" if r["doc_type"] in ("พระราชกฤษฎีกา", "พระราชบัญญัติ") else "ข้อ"
        paras = paragraphs(f)
        if tone_ratio("\n".join(paras)) < 0.06:
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": "ข้อความจาก PDF เพี้ยน (รอ OCR)",
                                   "title": r["title_th"][:90]})
            continue
        title, pre, provs, signed, tail, annex = parse(paras, kind, r["title_th"])
        gaz, pub = gazette(r["files"])
        sig_date = iso(DATE_RE.search(signed)) if signed and DATE_RE.search(signed) else None
        eff = r["effective_date"] or pub or sig_date
        warn = []
        if not r["effective_date"]:
            warn.append(f"วันใช้บังคับไม่มีในทะเบียน — ใช้ {'วันประกาศราชกิจจาฯ' if pub else 'วันที่ลงนาม' if sig_date else '-'}")
        if not eff:
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": "ไม่พบวันที่ใช้บังคับ", "title": r["title_th"][:90]})
            continue
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
        if f["ext"] == ".pdf":
            warn.append("ถอดข้อความจาก PDF — ตรวจคำที่ตัดผิด")
        code_issuer, issuer = issuer_of(title)
        year = (re.findall(r"25\d\d", th2ar(title)) or [str(int(eff[:4]) + 543)])[-1]
        seg = TYPE_SEG.get(r["doc_type"], "doc")
        key = "g" + r["reg_key"].replace(".", "-")
        eli = f"eli/th/{seg}/{code_issuer}/{year}/{key}"
        if eli in used_eli:
            eli += f"-r{r['sheet_row']}"
        used_eli.add(eli)
        part_of = lambda no: (f"{eli}/item-annex" if no == "__annex__" else
                              f"{eli}/{'sec' if kind == 'มาตรา' else 'clause'}-{no.replace('/', '-')}" if no else f"{eli}/item-1")
        provisions = []
        for p in provs:
            text = "\n".join(x for x in p["lines"] if x).strip()
            if not text:
                continue
            number = "แนบท้าย" if p["no"] == "__annex__" else f"{kind} {p['no']}" if p["no"] else "ทั้งฉบับ"
            provisions.append({"id": part_of(p["no"]), "number": number,
                               "part": p["part"], "sourcePage": None,
                               "versions": [{"version": 1, "validFrom": eff, "validTo": None, "text": text,
                                             "amendingInstrument": f"ตัวบทตั้งต้น ({gaz or 'ไฟล์จากฝ่ายกฎหมาย สำนักงาน กกพ.'})",
                                             "summary": None, "relations": []}]})
        if not provisions:
            rep["skipped"].append({"reg": r["reg_key"], "group": r["group_no"], "why": "แยกข้อความไม่ได้", "title": r["title_th"][:90]})
            continue
        short = re.sub(r"^\d+\s*\.\s*", "", f["name"].rsplit(".", 1)[0]).strip()[:100]
        inst = {"id": eli, "type": r["doc_type"], "title": re.sub(r"\s+", " ", title).strip(), "code": short,
                "issuer": issuer, "category": category_of(title), "publishedDate": pub, "effectiveDate": eff,
                "isDemo": False, "source": gaz or "ไฟล์จากฝ่ายกฎหมาย สำนักงาน กกพ.",
                "registerTitle": r["title_th"], "preamble": "\n".join(pre) or None, "signed": signed,
                "provisions": provisions,
                "sourceFiles": [{"sha256": x["sha256"], "name": x["name"], "role": "text" if x is f else "copy"}
                                for x in r["files"]],
                "importNotes": warn}
        fn = OUT / (eli.split("/", 2)[2].replace("/", "_") + ".json")
        fn.write_text(json.dumps([inst], ensure_ascii=False, indent=1), encoding="utf-8")
        rep["converted"].append({"reg": r["reg_key"], "eli": eli, "provisions": len(provisions), "src": f["ext"],
                                 "warn": warn, "title": inst["title"][:90]})
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("converted", len(rep["converted"]), "skipped", len(rep["skipped"]))


if __name__ == "__main__":
    main()
