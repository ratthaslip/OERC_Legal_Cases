"""แปลงข้อความระเบียบ/ประกาศจากราชกิจจานุเบกษา (ผลจาก tools/pdf_lines.py ... explicit) เป็น JSON สำหรับ load_laws

    python tools/pdf_lines.py reg.pdf /tmp/reg.tsv explicit
    python tools/reg_to_json.py /tmp/reg.tsv out.json --id eli/th/reg/erc/2552/appeal --code "ระเบียบการอุทธรณ์ 2552" \
        --category "การอุทธรณ์และข้อพิพาท" --authority 121

- ตัดหัวกระดาษราชกิจจานุเบกษา ("หน้า ..", "เล่ม .. ราชกิจจานุเบกษา ..") และใช้บรรทัดหัวเป็นแหล่งอ้างอิง
- หน่วยย่อยคือ "ข้อ" (รองรับ ข้อ ๑๙/๑), หัวข้อ หมวด/ส่วน/บทเฉพาะกาล, ย่อหน้าดูจากการเยื้องบรรทัด
- วันใช้บังคับ = วันถัดจากวันประกาศ (ตรวจว่าข้อ ๒ เขียนเช่นนั้นจริง ไม่เช่นนั้นต้องระบุ --effective)
"""
import argparse
import json
import re
from datetime import date, timedelta

TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
MONTHS = {m: i + 1 for i, m in enumerate("มกราคม กุมภาพันธ์ มีนาคม เมษายน พฤษภาคม มิถุนายน กรกฎาคม สิงหาคม กันยายน ตุลาคม พฤศจิกายน ธันวาคม".split())}
CLAUSE = re.compile(r"^ข้อ ?([๐-๙]+(?:/[๐-๙]+)?) ?(.*)$")
HEAD = re.compile(r"^(หมวด [๐-๙]+|ส่วนที่ [๐-๙]+|บทเฉพาะกาล)$")
GAZ = re.compile(r"^เล่ม ([๐-๙]+) (ตอน(?:พิเศษ)? [๐-๙]+ ?[ก-ฮ]?) ราชกิจจานุเบกษา ([๐-๙]+ \S+ [๐-๙]{4})$")


def thai_date(s: str) -> date:
    d, m, y = s.split()
    return date(int(y.translate(TH)) - 543, MONTHS[m], int(d.translate(TH)))


def join(prev: str, nxt: str) -> str:
    if not prev:
        return nxt
    if re.search(r"[)\]”\"0-9๐-๙A-Za-z.]$", prev) or nxt.startswith(("(", "“", "พ.ศ.")):
        return prev + " " + nxt
    return prev + nxt


def _spaces(lines: list[str]) -> tuple[str, set[int]]:
    """คืน (ข้อความไม่มีช่องว่าง, ตำแหน่งที่มีช่องว่างกลางบรรทัด)"""
    flat, sp = "", set()
    for line in lines:
        for i, ch in enumerate(line):
            if ch == " ":
                sp.add(len(flat))
            else:
                flat += ch
    return flat, sp


def merge_spaces(a: list[str], b: list[str]) -> str:
    fa, sa = _spaces(a)
    fb, sb = _spaces(b)
    if fa != fb:
        return " ".join(" ".join(b).split())
    sp = sa | sb
    out = "".join((" " if i in sp else "") + ch for i, ch in enumerate(fa)).strip()
    return re.sub(r"(?<=\S)พ\.ศ\.", " พ.ศ.", out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--id", required=True); ap.add_argument("--code", required=True)
    ap.add_argument("--category", default="ระเบียบ กกพ."); ap.add_argument("--type", default="ระเบียบ")
    ap.add_argument("--issuer", default="คณะกรรมการกำกับกิจการพลังงาน")
    ap.add_argument("--effective", help="YYYY-MM-DD ถ้าไม่ใช่วันถัดจากวันประกาศ")
    a = ap.parse_args()

    rows = []
    for line in open(a.src, encoding="utf-8").read().splitlines():
        p, x, sz, t = line.split("\t", 3)
        if t.strip():
            rows.append((int(p), int(x), float(sz), t.strip()))
    gaz = next(GAZ.match(t) for _, _, _, t in rows if GAZ.match(t))
    pub = thai_date(gaz.group(3))
    body = [r for r in rows if not GAZ.match(r[3]) and not re.match(r"^หน้า [๐-๙]+$", r[3])]
    margin = min(x for _, x, _, _ in body)
    indent = margin + 25

    # ชื่อเรื่อง = บรรทัดก่อน "อาศัยอำนาจ" / "โดยที่"
    start = next(i for i, r in enumerate(body) if r[3].startswith(("อาศัยอำนาจ", "โดยที่")))
    title = " ".join(t for _, _, _, t in body[:start])
    title = re.sub(r"\s+", " ", title).replace(" พ.ศ.", " พ.ศ.")
    preamble = ""
    clauses, chapter, part, cur = [], None, None, None
    i = start
    while i < len(body):
        p, x, sz, t = body[i]
        if t.startswith("ประกาศ ณ วันที่"):
            break
        if HEAD.match(t) and x > indent + 40:
            ttl = body[i + 1][3] if i + 1 < len(body) and body[i + 1][1] > indent + 40 and not HEAD.match(body[i + 1][3]) else ""
            i += 2 if ttl else 1
            if t.startswith("ส่วนที่"):
                part = f"{t} {ttl}".strip()
            else:
                chapter, part = f"{t} {ttl}".strip(), None
            continue
        m = CLAUSE.match(t)
        if m and x >= indent - 5:
            cur = {"num": m.group(1), "paras": [m.group(2)], "part": " · ".join(v for v in [chapter, part] if v), "page": p,
                   "lines": [m.group(2)]}
            clauses.append(cur)
        elif cur is None:
            preamble = join(preamble, t) if x < indent - 5 else (preamble + "\n" + t if preamble else t)
        elif x >= indent - 5:
            cur["paras"].append(t); cur["lines"].append(t)
        else:
            cur["paras"][-1] = join(cur["paras"][-1], t); cur["lines"].append(t)
        i += 1
    signed = next((t for _, _, _, t in body if t.startswith("ประกาศ ณ วันที่")), None)
    # ชื่อเรื่องอยู่ 2 ที่ (หัวเรื่องหลายบรรทัด และใน "ข้อ ๑ ... เรียกว่า “...”") ซึ่งตัดบรรทัดคนละตำแหน่ง
    # ช่องว่างกลางบรรทัดเชื่อถือได้ ช่องว่างที่รอยต่อบรรทัดไม่รู้ → รวมช่องว่างกลางบรรทัดจากทั้งสองแหล่ง
    c1 = next((c for c in clauses if c["num"] == "๑"), None)
    if c1:
        raw = "\n".join(c1["lines"])
        m1 = re.search(r"เรียกว่า ?“(.+?)”", raw, re.S)
        if m1:
            title = merge_spaces([t for _, _, _, t in body[:start]], m1.group(1).split("\n"))
    c2 = next((c for c in clauses if c["num"] == "๒"), None)
    if not a.effective and not (c2 and "วันถัดจากวันประกาศในราชกิจจานุเบกษา" in "".join(c2["paras"])):
        raise SystemExit("ข้อ ๒ ไม่ได้กำหนดให้ใช้บังคับวันถัดจากวันประกาศ — ระบุ --effective")
    eff = a.effective or (pub + timedelta(days=1)).isoformat()
    source = f"ราชกิจจานุเบกษา เล่ม {gaz.group(1)} {gaz.group(2)} วันที่ {gaz.group(3)}"

    provs = []
    for c in clauses:
        n_ar = c["num"].translate(TH)
        text = "\n".join(x for x in c["paras"] if x)
        text = re.sub(r"(มาตรา|ข้อ)(?=[๐-๙])", r"\1 ", text)
        provs.append({"id": f"{a.id}/clause-{n_ar.replace('/', '-')}", "number": f"ข้อ {n_ar}",
                      "part": c["part"] or None, "sourcePage": c["page"],
                      "versions": [{"version": 1, "validFrom": eff, "validTo": None, "text": text,
                                    "amendingInstrument": f"ตัวบทตั้งต้น ({source})", "summary": c["part"] or None,
                                    "relations": []}]})
    data = [{"id": a.id, "type": a.type, "title": title, "code": a.code, "issuer": a.issuer, "category": a.category,
             "publishedDate": pub.isoformat(), "effectiveDate": eff, "isDemo": False, "source": source,
             "preamble": preamble, "signed": signed, "provisions": provs}]
    json.dump(data, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    nums = [int(c["num"].split("/")[0].translate(TH)) for c in clauses]
    missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
    print(f"{title}\n  {source} → ใช้บังคับ {eff} · {len(clauses)} ข้อ (สูงสุด {max(nums)}) ขาดเลข: {missing or '-'}")


if __name__ == "__main__":
    main()
