"""พ.ร.บ.จัดตั้งศาลปกครองและวิธีพิจารณาคดีปกครอง พ.ศ. 2542 (ฉบับรวมในหนังสือรวมกฎหมายของสำนักงานศาลปกครอง)
→ JSON สำหรับ load_laws

    python tools/pdf_lines.py book.pdf /tmp/court.tsv explicit 31-146 anycolor
    python tools/court_act_to_json.py /tmp/court.tsv backend/app/seed/laws/act-2542-admin-court.json

ฉบับรวมแสดงเฉพาะถ้อยคำปัจจุบัน (แก้ไขถึงฉบับที่ ๑๔ พ.ศ. ๒๕๖๔) และบอกการแก้ไขในเชิงอรรถ
- มาตราที่ไม่มีเชิงอรรถ: รุ่นเดียว มีผลตั้งแต่ ๑๑ ต.ค. ๒๕๔๒
- มาตราที่ "เพิ่มโดย" ทั้งมาตรา: เริ่มมีผลเมื่อฉบับแก้ไขนั้นใช้บังคับ
- มาตราที่แก้ไข (ทั้งมาตราหรือบางวรรค/นิยาม): รุ่นเดิมเป็นข้อความแจ้งว่าไม่มีถ้อยคำเดิมในฉบับรวม + รุ่นปัจจุบัน
  เริ่มเมื่อฉบับแก้ไขล่าสุดที่กระทบมาตรานั้นใช้บังคับ
วันใช้บังคับของฉบับแก้ไข = วันถัดจากวันประกาศ (ประมาณการ: หนังสือไม่ได้พิมพ์มาตรา ๒ ของฉบับแก้ไข ต้องยืนยัน)
"""
import json
import re
import sys
from datetime import date, timedelta

TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
MONTHS = {m: i + 1 for i, m in enumerate("มกราคม กุมภาพันธ์ มีนาคม เมษายน พฤษภาคม มิถุนายน กรกฎาคม สิงหาคม กันยายน ตุลาคม พฤศจิกายน ธันวาคม".split())}
ELI = "eli/th/act/2542/admin-court"
ORIGINAL_EFFECTIVE = "1999-10-11"  # ม.2 วันถัดจากวันประกาศ (ราชกิจจานุเบกษา เล่ม ๑๑๖ ตอนที่ ๙๔ ก ๑๐ ต.ค. ๒๕๔๒)
SEC = re.compile(r"^มาตรา ?([๐-๙]+(?:/[๐-๙]+)?) ?(.*)$")
HEAD = re.compile(r"^(หมวด [๐-๙]+(?:/[๐-๙]+)?|ส่วนที่ [๐-๙]+(?:/[๐-๙]+)?|บทเฉพาะกาล)$")
NOTE = re.compile(r"ความในมาตรา ([๐-๙]+(?:/[๐-๙]+)?)(.*?)(แก้ไขเพิ่มเติมโดย|เพิ่มโดย|ถูกยกเลิกโดย|ยกเลิกโดย)"
                  r"มาตรา [๐-๙]+ (?:แห่ง)?พระราชบัญญัติจัดตั้งศาลปกครองและวิธีพิจารณาคดีปกครอง \(ฉบับที่ ([๐-๙]+)\)")


def thai_date(s: str) -> date:
    d, m, y = s.split()
    return date(int(y.translate(TH)) - 543, MONTHS[m], int(d.translate(TH)))


def join(prev: str, nxt: str) -> str:
    if not prev:
        return nxt
    if re.search(r"[)\]”\"0-9๐-๙A-Za-z.]$", prev) or nxt.startswith(("(", "“", "พ.ศ.")) or re.match(r"[๐-๙0-9]", nxt):
        return prev + " " + nxt
    return prev + nxt


def key(n: str) -> tuple:
    a, _, b = n.translate(TH).partition("/")
    return int(a), int(b or 0)


def main(src: str, out: str) -> None:
    rows = []
    for line in open(src, encoding="utf-8").read().splitlines():
        p, x, sz, t = line.split("\t", 3)
        if t.strip():
            rows.append((int(p), int(x), float(sz), t.strip()))
    body = [r for r in rows if r[2] >= 17.5]
    notes = [r for r in rows if 15.5 <= r[2] < 17.5 and not re.fullmatch(r"[๐-๙0-9]+", r[3])]

    note_text = ""
    for _, _, _, t in notes:
        note_text = join(note_text, t)
    note_text = re.sub(r"\s+", " ", note_text)
    # ฉบับแก้ไขและวันประกาศ (จากหมายเหตุท้ายพระราชบัญญัติ)
    amend = {}
    for m in re.finditer(r"\(ฉบับที่ ([๐-๙]+)\) พ\.ศ\. ([๐-๙]{4}) ประกาศในราชกิจจานุเบกษา เล่ม ([๐-๙]+) ตอนที่ ([๐-๙]+ ?ก) วันที่ ([๐-๙]+ \S+ [๐-๙]{4})", note_text):
        no = m.group(1)
        pub = thai_date(m.group(5))
        amend[no] = {"year": m.group(2), "pub": pub, "eff": pub + timedelta(days=1),
                     "label": f"พระราชบัญญัติจัดตั้งศาลปกครองและวิธีพิจารณาคดีปกครอง (ฉบับที่ {no}) พ.ศ. {m.group(2)}",
                     "gazette": f"ราชกิจจานุเบกษา เล่ม {m.group(3)} ตอนที่ {m.group(4)} วันที่ {m.group(5)}"}
    changes: dict[str, list] = {}
    for m in NOTE.finditer(note_text):
        changes.setdefault(m.group(1), []).append({"scope": m.group(2).strip(), "kind": m.group(3), "no": m.group(4)})

    secs, chapter, part, cur, i = [], None, None, None, 0
    started = False
    while i < len(body):
        p, x, sz, t = body[i]
        if t.startswith("ผู้รับสนองพระบรมราชโองการ"):
            break
        if HEAD.match(t) and x >= 110:
            ttl = ""
            if i + 1 < len(body) and body[i + 1][1] >= 100 and not SEC.match(body[i + 1][3]) and not HEAD.match(body[i + 1][3]):
                ttl = body[i + 1][3]
                i += 1
            if t.startswith("ส่วนที่"):
                part = f"{t} {ttl}".strip()
            else:
                chapter, part = f"{t} {ttl}".strip(), None
            i += 1
            continue
        m = SEC.match(t)
        if m and 60 <= x <= 80:
            started = True
            cur = {"num": m.group(1), "paras": [m.group(2)], "part": " · ".join(v for v in [chapter, part] if v), "page": p}
            secs.append(cur)
        elif started and cur:
            if x >= 60:
                cur["paras"].append(t)
            else:
                cur["paras"][-1] = join(cur["paras"][-1], t)
        i += 1

    provs, unknown = [], set()
    for s in secs:
        n_ar = s["num"].translate(TH)
        text = "\n".join(x for x in s["paras"] if x)
        text = re.sub(r"มาตรา(?=[๐-๙])", "มาตรา ", text)
        base = {"summary": s["part"] or None, "relations": []}
        ch = changes.get(s["num"], [])
        for c in ch:
            if c["no"] not in amend:
                unknown.add(c["no"])
        ch = [c for c in ch if c["no"] in amend]
        original = f"ตัวบทตั้งต้น (ราชกิจจานุเบกษา เล่ม ๑๑๖ ตอนที่ ๙๔ ก วันที่ ๑๐ ตุลาคม ๒๕๔๒)"
        whole_insert = [c for c in ch if c["kind"] == "เพิ่มโดย" and not c["scope"]]
        notes_rel = [{"type": c["kind"].replace("โดย", "โดย"), "target": f"{amend[c['no']]['label']}{(' — ' + c['scope']) if c['scope'] else ''}"} for c in ch]
        if not ch:
            versions = [{**base, "version": 1, "validFrom": ORIGINAL_EFFECTIVE, "validTo": None, "text": text,
                         "amendingInstrument": original}]
        else:
            latest = max(ch, key=lambda c: int(c["no"].translate(TH)))
            a = amend[latest["no"]]
            lbl = f"{a['label']} ({a['gazette']}; วันใช้บังคับเป็นค่าประมาณ ต้องยืนยัน)"
            if whole_insert and len(ch) == 1:
                versions = [{**base, "version": 1, "validFrom": a["eff"].isoformat(), "validTo": None, "text": text,
                             "amendingInstrument": lbl, "relations": notes_rel}]
            elif text.strip() == "(ยกเลิก)":
                versions = [{**base, "version": 1, "validFrom": ORIGINAL_EFFECTIVE, "validTo": (a["eff"] - timedelta(days=1)).isoformat(),
                             "text": f"[ถ้อยคำเดิมของมาตรา {s['num']} ก่อนถูกยกเลิกไม่มีในฉบับรวม — ตรวจจากราชกิจจานุเบกษา]",
                             "amendingInstrument": original, "relations": notes_rel}]
            else:
                first_from = ORIGINAL_EFFECTIVE
                ins = [c for c in whole_insert]
                if ins:
                    first_from = amend[min(ins, key=lambda c: int(c["no"].translate(TH)))["no"]]["eff"].isoformat()
                versions = [
                    {**base, "version": 1, "validFrom": first_from, "validTo": (a["eff"] - timedelta(days=1)).isoformat(),
                     "text": f"[ถ้อยคำของมาตรา {s['num']} ก่อนแก้ไขโดย{a['label']} ไม่มีในฉบับรวม — ตรวจจากราชกิจจานุเบกษา]",
                     "amendingInstrument": original if not ins else lbl, "relations": notes_rel},
                    {**base, "version": 2, "validFrom": a["eff"].isoformat(), "validTo": None, "text": text,
                     "amendingInstrument": lbl, "relations": notes_rel},
                ]
                if versions[0]["validFrom"] > versions[0]["validTo"]:
                    versions = versions[1:]
                    versions[0]["version"] = 1
        provs.append({"id": f"{ELI}/sec-{n_ar.replace('/', '-')}", "number": f"มาตรา {n_ar}", "part": s["part"] or None,
                      "sourcePage": s["page"], "versions": versions})
    provs.sort(key=lambda p: key(p["number"].split(" ", 1)[1]))
    data = [{
        "id": ELI, "type": "พระราชบัญญัติ", "title": "พระราชบัญญัติจัดตั้งศาลปกครองและวิธีพิจารณาคดีปกครอง พ.ศ. 2542",
        "code": "พ.ร.บ.จัดตั้งศาลปกครองฯ 2542", "issuer": "รัฐสภา", "category": "กฎหมายปกครอง",
        "publishedDate": "1999-10-10", "effectiveDate": ORIGINAL_EFFECTIVE, "isDemo": False,
        "source": "ฉบับรวมแก้ไขถึงฉบับที่ ๑๔ พ.ศ. ๒๕๖๔ จากหนังสือรวมกฎหมายของสำนักงานศาลปกครอง",
        "amendments": [{"no": k, **{kk: (vv.isoformat() if isinstance(vv, date) else vv) for kk, vv in v.items()}}
                       for k, v in sorted(amend.items(), key=lambda kv: int(kv[0].translate(TH)))],
        "provisions": provs,
    }]
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    nums = [key(s["num"])[0] for s in secs]
    amended = sum(1 for p in provs if len(p["versions"]) > 1 or p["versions"][0]["validFrom"] != ORIGINAL_EFFECTIVE)
    print(f"มาตรา {len(secs)} (สูงสุด {max(nums)}), ขาดเลข: {sorted(set(range(1, max(nums) + 1)) - set(nums)) or '-'}")
    print(f"ฉบับแก้ไขที่พบวันประกาศ: {len(amend)} ({', '.join(sorted(amend, key=lambda k: int(k.translate(TH))))})"
          f" · มาตราที่มีการแก้ไข/เพิ่ม {amended} · เชิงอรรถอ้างฉบับที่ไม่พบวันประกาศ: {sorted(unknown) or '-'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
