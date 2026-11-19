"""Step 2: split each instrument into retrievable units (มาตรา / ข้อ) with metadata.

Unit ids follow the labelling guide: <doc>@<BE year>#<unit>, e.g. T220@2551#ข้อ8.
Until the registry row (T###) is known, <doc> is a provisional id built from the
registry folder number and the source file id.
"""
import json, pathlib, re, sys
from datetime import date, timedelta

from .textnorm import thai_digits_to_arabic as a
THAI_NUM = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")

TH_MONTHS = {m: i + 1 for i, m in enumerate(
    "มกราคม กุมภาพันธ์ มีนาคม เมษายน พฤษภาคม มิถุนายน กรกฎาคม สิงหาคม กันยายน ตุลาคม พฤศจิกายน ธันวาคม".split())}
D = r"[0-9๐-๙]"
MONTH_RE = "|".join(TH_MONTHS)

GAZ_LINE = re.compile(
    rf"^(หน้า\s*{D}+|เล่ม\s*{D}+.*|ตอน(พิเศษ)?\s*{D}+.*|ราชกิจจานุเบกษา|{D}{{1,2}}\s*({MONTH_RE})\s*{D}{{4}})$")
GAZ_HDR = re.compile(
    rf"เล่ม\s*({D}+)\s*ตอน(พิเศษ)?\s*({D}+)\s*([กขคง])?.*?ราชกิจจานุเบกษา\s*({D}{{1,2}})\s*({MONTH_RE})\s*({D}{{4}})",
    re.S)
UNIT_START = re.compile(rf"^(มาตรา|ข้อ)\s*({D}+(?:/{D}+)?)(?=\s|$)")
HEADING = re.compile(rf"^(หมวด|ส่วนที่)\s*({D}+)\s*(.*)$")
SIGNED = re.compile(rf"(ประกาศ|ให้ไว้)\s*ณ\s*วันที่\s*({D}{{1,2}})\s*({MONTH_RE})\s*(?:พ\.ศ\.)?\s*({D}{{4}})")
TITLE_YEAR = re.compile(rf"พ\.ศ\.\s*({D}{{4}})")
EDITION = re.compile(rf"\(ฉบับที่\s*({D}+)\)")
BODY_START = re.compile(r"^(โดยที่|อาศัยอำนาจ|ภูมิพลอดุลยเดช|สมเด็จพระ|พระบาทสมเด็จ)")
NEXT_DAY = re.compile(r"ใช้บังคับ(ตั้งแต่|เมื่อพ้นกำหนด)?\s*วันถัดจากวันประกาศ")
SAME_DAY = re.compile(r"ใช้บังคับตั้งแต่วันประกาศ")


def be_date(d, m, y):
    try:
        return date(int(a(y)) - 543, TH_MONTHS[m], int(a(d)))
    except Exception:
        return None


def strip_gazette_lines(lines):
    return [ln for ln in lines if not GAZ_LINE.match(ln.strip())]


def quote_delta(s):
    return s.count("“") - s.count("”") + (s.count('"') % 2)


def parse(text, doc_id):
    meta = {}
    m = GAZ_HDR.search(text[:600])
    if m:
        meta.update(gazette_volume=int(a(m.group(1))), gazette_part=("พิเศษ " if m.group(2) else "") + a(m.group(3)) +
                    (f" {m.group(4)}" if m.group(4) else ""))
        g = be_date(m.group(5), m.group(6), m.group(7))
        meta["gazette_date"] = g.isoformat() if g else None
    lines = strip_gazette_lines([ln.strip() for ln in text.split("\n")])
    lines = [ln for ln in lines if ln]
    # title = lines before body start (max 8 lines)
    title, i = [], 0
    while i < len(lines) and i < 8 and not BODY_START.match(lines[i]) and not UNIT_START.match(lines[i]):
        title.append(lines[i]); i += 1
    meta["title"] = re.sub(r"\s+", " ", " ".join(title)).strip()
    ty = TITLE_YEAR.search(meta["title"])
    meta["year_be"] = int(a(ty.group(1))) if ty else None
    ed = EDITION.search(meta["title"])
    meta["edition"] = int(a(ed.group(1))) if ed else 1
    sg = SIGNED.search(text)
    if sg:
        s = be_date(sg.group(2), sg.group(3), sg.group(4))
        meta["signed_date"] = s.isoformat() if s else None
    if meta.get("gazette_date"):
        g = date.fromisoformat(meta["gazette_date"])
        if NEXT_DAY.search(text):
            meta["effective_date"], meta["effective_rule"] = (g + timedelta(days=1)).isoformat(), "next_day"
        elif SAME_DAY.search(text):
            meta["effective_date"], meta["effective_rule"] = g.isoformat(), "same_day"
    year = meta["year_be"] or "na"
    # Acts and royal decrees are divided into มาตรา; subordinate rules into ข้อ.
    unit_kind = "มาตรา" if re.match(r"(พระราชบัญญัติ|พระราชกฤษฎีกา)", meta["title"]) else "ข้อ"
    meta["unit_kind"] = unit_kind
    units, cur, path, depth, last = [], None, [], 0, 0
    preamble = []
    for ln in lines[i:]:
        if SIGNED.search(ln) and depth == 0 and cur:
            cur["text"].append(ln)
            units.append(cur); cur = None
            preamble = None  # stop: annex follows
            continue
        h = HEADING.match(ln) if depth == 0 else None
        um = UNIT_START.match(ln) if depth == 0 else None
        if um and um.group(1) != unit_kind:
            um = None
        if um:
            raw = a(um.group(2)); base = int(raw.split("/")[0]); sub = "/" in raw
            # Krisdika texts glue a footnote marker onto the number ("มาตรา ๒๑" = ม.2 + fn 1)
            nxt = str(last + 1)
            if (not sub and base != last + 1 and str(base).startswith(nxt)
                    and len(str(base)) - len(nxt) in (1, 2)):
                base = last + 1; um_num = str(base)
            else:
                um_num = None
            # accept only the next number (or a /n insert of the current one);
            # anything else is a cross-reference that happens to start a line
            if not ((sub and base == last) or base == last + 1 or (last == 0 and base == 1)):
                um = None
            else:
                last = base
        if h:
            label = f"{h.group(1)} {a(h.group(2))} {h.group(3)}".strip()
            # หมวด opens a new top level; ส่วน nests under the current หมวด
            path = [label] if h.group(1).startswith("หมวด") else [p for p in path if p.startswith("หมวด")] + [label]
        elif depth == 0 and ln.strip() == "บทเฉพาะกาล":
            path = ["บทเฉพาะกาล"]
        elif um:
            if cur: units.append(cur)
            kind, num = um.group(1), (um_num or a(um.group(2)))
            if um_num:  # drop the glued footnote marker from the text as well
                ln = ln[:um.start(2)] + nxt.translate(THAI_NUM) + ln[um.end(2):]
            uid = f"{'ม' if kind == 'มาตรา' else 'ข้อ'}{num}"
            cur = {"unit": uid, "kind": kind, "number": num, "path": list(path), "text": [ln]}
        elif cur is not None:
            cur["text"].append(ln)
        elif preamble is not None:
            preamble.append(ln)
        depth = max(0, depth + quote_delta(ln))
    if cur: units.append(cur)
    out = []
    if preamble and not units:
        # instruments without numbered clauses (fee schedules, charters, one-paragraph
        # notices) are kept as a single retrievable unit
        out.append({"unit": "ทั้งฉบับ", "kind": "whole", "number": None, "path": [], "text": preamble})
    elif preamble:
        out.append({"unit": "อารัมภบท", "kind": "preamble", "number": None, "path": [], "text": preamble})
    seen = {}
    for u in units:
        n = seen.get(u["unit"], 0); seen[u["unit"]] = n + 1
        if n:  # duplicate number (e.g. บทเฉพาะกาล restarts) -> suffix
            u["unit"] = f"{u['unit']}_{n + 1}"
        out.append(u)
    for u in out:
        u["id"] = f"{doc_id}@{year}#{u['unit']}"
        u["text"] = "\n".join(u["text"]).strip()
        # PDF lines wrap mid-phrase; for indexing, join Thai-to-Thai breaks with no space
        u["text_flat"] = flatten(u["text"])
    return meta, out


def flatten(text: str) -> str:
    """Join wrapped PDF lines. A line clearly shorter than the block's usual width
    ends a paragraph (วรรค) and keeps a space; other Thai-to-Thai breaks are wraps
    inside a phrase and are joined with no space."""
    lines = text.split("\n")
    if len(lines) < 3:
        return " ".join(lines)
    width = sorted(len(l) for l in lines)[int(len(lines) * 0.75)]
    out = lines[0]
    for prev, ln in zip(lines, lines[1:]):
        wrap = len(prev) >= 0.85 * width and re.match(r"[\u0e01-\u0e5b]", ln) and re.search(r"[\u0e01-\u0e5b]$", prev)
        out += ("" if wrap else " ") + ln
    return out


def check_sequence(units):
    nums = [int(u["number"].split("/")[0]) for u in units if u["number"] and "_" not in u["unit"]]
    gaps = [(x, y) for x, y in zip(nums, nums[1:]) if y not in (x, x + 1)]
    return {"n_units": len(nums), "first": nums[0] if nums else None, "seq_gaps": gaps[:5]}


def main(work_dir):
    work = pathlib.Path(work_dir)
    docs = [json.loads(l) for l in open(work / "docs.jsonl", encoding="utf-8")]
    out_docs, n_units = [], 0
    with open(work / "units.jsonl", "w", encoding="utf-8") as fu:
        for d in docs:
            if not d["source"]:
                out_docs.append(d); continue
            doc_id = f"G{d['group'] or 'x'}-{d['file_id'][:6]}"
            text = (work / "text" / f"{d['file_id']}.txt").read_text(encoding="utf-8")
            meta, units = parse(text, doc_id)
            d.update(doc_id=doc_id, **meta, **check_sequence(units))
            for u in units:
                u["doc_id"] = doc_id
                fu.write(json.dumps(u, ensure_ascii=False) + "\n")
            n_units += len(units)
            out_docs.append(d)
    with open(work / "docs.jsonl", "w", encoding="utf-8") as fh:
        for d in out_docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"units={n_units}")


if __name__ == "__main__":
    main(sys.argv[1])
