"""ประกอบตัวบทฉบับรวมแบบแบ่งรุ่นตามวันที่: นำฉบับแก้ไข (JSON จาก reg_to_json.py) ไปใช้กับฉบับหลัก

    python tools/apply_amendments.py base.json amend1.json [amend2.json ...]   # เขียนทับ base.json

รู้จักถ้อยคำมาตรฐานของการแก้ไข:
- "ให้เพิ่มข้อความต่อไปนี้เป็นข้อ ๑๖/๑ ..." + “ข้อ ๑๖/๑ ...”              → เพิ่มข้อใหม่ มีผลตั้งแต่ฉบับแก้ไขใช้บังคับ
- "ให้ยกเลิกความในข้อ ๒๑ ... และให้ใช้ความต่อไปนี้แทน" + “ข้อ ๒๑ ...”   → ปิดรุ่นเดิม เพิ่มรุ่นใหม่
- "ให้ยกเลิกข้อ ๙ ..." (ไม่มีความใหม่)                                      → ปิดรุ่นเดิม (ข้อถูกยกเลิก)
ถ้อยคำที่ไม่รู้จักจะหยุดพร้อมแจ้ง เพื่อให้คนตรวจแทนการเดา
"""
import json
import re
import sys
from datetime import date, timedelta

TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
NUM = r"([๐-๙]+(?:/[๐-๙]+)?)"


def key(n: str) -> tuple:
    a, _, b = n.translate(TH).partition("/")
    return int(a), int(b or 0)


def main(base_path: str, amend_paths: list[str]) -> None:
    base = json.load(open(base_path, encoding="utf-8"))
    inst = base[0]
    by_num = {p["number"].split(" ", 1)[1]: p for p in inst["provisions"]}
    applied = inst.setdefault("amendedBy", [])
    for path in amend_paths:
        am = json.load(open(path, encoding="utf-8"))[0]
        if any(a["id"] == am["id"] for a in applied):
            print(f"ข้าม {am['code']} (ใช้แล้ว)")
            continue
        eff = date.fromisoformat(am["effectiveDate"])
        day_before = (eff - timedelta(days=1)).isoformat()
        label = f"{am['title']} ({am['source']})"
        for p in am["provisions"]:
            text = p["versions"][0]["text"]
            head = text.split("\n", 1)[0]
            m_add = re.search(rf"ให้เพิ่มข้อความต่อไปนี้เป็นข้อ {NUM}", head)
            m_rep = re.search(rf"ให้ยกเลิกความในข้อ {NUM}.*ให้ใช้ความต่อไปนี้แทน", head)
            m_del = re.search(rf"^ให้ยกเลิกข้อ {NUM}", head)
            if not (m_add or m_rep or m_del):
                if p["number"] not in ("ข้อ 1", "ข้อ 2"):
                    print(f"  ⚠ {am['code']} {p['number']} ไม่ใช่ถ้อยคำแก้ไขที่รู้จัก — ไม่ได้นำไปใช้")
                continue
            num = (m_add or m_rep or m_del).group(1)
            n_ar = num.translate(TH)
            new_text = None
            if m_add or m_rep:
                q = re.search(r"“ข้อ ?" + re.escape(num) + r" ?(.*)”\s*$", text, re.S)
                if not q:
                    raise SystemExit(f"{am['code']} {p['number']}: หาความใหม่ของข้อ {num} ในเครื่องหมายคำพูดไม่พบ")
                new_text = q.group(1).strip()
            ver = {"validFrom": am["effectiveDate"], "validTo": None, "text": new_text,
                   "amendingInstrument": label, "summary": None,
                   "relations": [{"type": "แก้ไขเพิ่มเติมโดย", "target": f"{am['title']} {p['number']}"}]}
            if m_add:
                if n_ar in by_num:
                    raise SystemExit(f"ข้อ {num} มีอยู่แล้ว แต่ฉบับแก้ไขบอกให้เพิ่ม")
                parent = by_num.get(n_ar.split("/")[0])
                prov = {"id": f"{inst['id']}/clause-{n_ar.replace('/', '-')}", "number": f"ข้อ {n_ar}",
                        "part": parent["part"] if parent else None, "sourcePage": None,
                        "versions": [{**ver, "version": 1, "summary": parent["part"] if parent else None}]}
                by_num[n_ar] = prov
                print(f"  + เพิ่มข้อ {n_ar} มีผล {am['effectiveDate']}")
            else:
                prov = by_num.get(n_ar)
                if not prov:
                    raise SystemExit(f"ไม่พบข้อ {num} ในฉบับหลัก")
                cur = prov["versions"][-1]
                if cur["validTo"]:
                    raise SystemExit(f"ข้อ {num} รุ่นล่าสุดถูกปิดไปแล้ว")
                cur["validTo"] = day_before
                cur.setdefault("relations", []).append({"type": "ถูกแก้ไขโดย" if m_rep else "ถูกยกเลิกโดย",
                                                        "target": f"{am['title']} {p['number']}"})
                if m_rep:
                    prov["versions"].append({**ver, "version": cur["version"] + 1, "summary": cur.get("summary")})
                    print(f"  ~ แก้ข้อ {n_ar}: รุ่น {cur['version']} ถึง {day_before}, รุ่น {cur['version'] + 1} ตั้งแต่ {am['effectiveDate']}")
                else:
                    print(f"  - ยกเลิกข้อ {n_ar} ตั้งแต่ {am['effectiveDate']}")
        applied.append({"id": am["id"], "title": am["title"], "effectiveDate": am["effectiveDate"], "source": am["source"]})
    inst["provisions"] = sorted(by_num.values(), key=lambda p: key(p["number"].split(" ", 1)[1]))
    json.dump(base, open(base_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
