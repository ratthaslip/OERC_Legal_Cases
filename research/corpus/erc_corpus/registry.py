"""Step 3: link each extracted document to its row in the office's registry of
subordinate legislation (legal-files-inventory.xlsx, sheet "รายการทะเบียน") and
rewrite provisional ids (G<group>-<file>) to the guide's form T<row>@<year>#<unit>.

Matching order:
  1. registry number (= Drive folder) and file name both match
  2. registry number matches and the registry has only one row with that number
  3. title similarity >= 0.85 against every registry title (files outside folders)
The Act itself is not in the registry and gets the fixed id ACT2550.

The registry is an internal document: only row numbers leave this step; the
workbook itself is never written to the output.
"""
import difflib, json, pathlib, re, sys, unicodedata

import openpyxl

from .textnorm import thai_digits_to_arabic as a

SHEET = "รายการทะเบียน"


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFC", a(t or ""))
    t = t.replace("คณะกรรมการกำกับกิจการพลังงาน", "กกพ").replace("กกพ.", "กกพ")
    return re.sub(r"[\s\.\(\)“”\"']+", "", t)


def norm_file(f: str) -> str:
    stem = pathlib.PurePath(f).stem
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", stem)).strip().lower()


def load_registry(xlsx):
    ws = openpyxl.load_workbook(xlsx, read_only=True)[SHEET]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    col = {h: i for i, h in enumerate(hdr)}
    out = []
    for r in rows[1:]:
        if r[col["ลำดับแถว"]] is None:
            continue
        out.append({
            "row": int(r[col["ลำดับแถว"]]),
            "reg_no": str(r[col["เลขทะเบียน"]] or "").strip(),
            "type": r[col["ประเภท"]],
            "status": r[col["สถานะ"]],
            "title": r[col["ชื่อเรื่อง"]] or "",
            "files": {norm_file(f) for f in str(r[col["ชื่อไฟล์"]] or "").splitlines() if f.strip()},
        })
    return out


def match(doc, reg, by_no):
    if doc.get("source") == "Energy20.pdf" or doc.get("title", "").startswith("พระราชบัญญัติ การประกอบกิจการพลังงาน"):
        return "ACT2550", "act", 1.0
    g, f = doc.get("group", ""), norm_file(doc.get("source") or doc["files"][0])
    cands = by_no.get(g, [])
    for r in cands:
        if f in r["files"] or any(norm_file(x) in r["files"] for x in doc["files"]):
            return f"T{r['row']}", "folder+file", 1.0
    if len(cands) == 1:
        return f"T{cands[0]['row']}", "folder", 0.9
    t = norm_title(doc.get("title", ""))
    if len(t) >= 15:
        pool = cands or reg
        best = max(pool, key=lambda r: difflib.SequenceMatcher(None, t, norm_title(r["title"])).ratio())
        score = difflib.SequenceMatcher(None, t, norm_title(best["title"])).ratio()
        if score >= 0.85:
            return f"T{best['row']}", "title", round(score, 3)
    return None, "unmatched", 0.0


def main(work, xlsx):
    work = pathlib.Path(work)
    reg = load_registry(xlsx)
    by_no = {}
    for r in reg:
        by_no.setdefault(r["reg_no"], []).append(r)
    reg_by_row = {f"T{r['row']}": r for r in reg}

    docs = [json.loads(l) for l in open(work / "docs.jsonl", encoding="utf-8")]
    remap = {}
    for d in docs:
        rid, how, score = match(d, reg, by_no)
        d["registry_id"], d["registry_match"], d["registry_score"] = rid, how, score
        if rid and rid in reg_by_row:
            d["registry_status"] = reg_by_row[rid]["status"]
            d["registry_type"] = reg_by_row[rid]["type"]

    # One registry row can hold several files: the same text in two formats, or
    # several versions (e.g. NGO selection rules of 2550/2553/2568). Versions stay
    # apart through the @year part of the unit id; a scanned copy whose file name
    # carries the same year and edition as an extracted file is a format duplicate.
    def ver(d):
        f = norm_file(d.get("source") or d["files"][0])
        yy = re.findall(r"(?<!\d)(\d{2})(?!\d)", f)
        ed = re.search(r"(?:ฉบับที่|ฉบับ|ฉ)\s*(\d+)", f)
        return (yy[-1] if yy else None, ed.group(1) if ed else "1")

    groups = {}
    for d in docs:
        if d["registry_id"]:
            groups.setdefault(d["registry_id"], []).append(d)
    for rid, ds in groups.items():
        texts = [d for d in ds if not d.get("needs_ocr")]
        for d in ds:
            if d.get("needs_ocr") and any(ver(t) == ver(d) for t in texts):
                d["duplicate_of"] = next(t.get("file_id") for t in texts if ver(t) == ver(d))
        # two extracted copies of the same version (Gazette PDF and office Word file,
        # or the same rule shipped in two zips): keep the Gazette copy, else the longer one
        byver = {}
        for t in texts:
            byver.setdefault((t.get("year_be"), t.get("edition")), []).append(t)
        for same in byver.values():
            if len(same) > 1 and same[0].get("year_be"):
                same.sort(key=lambda t: (not t.get("gazette_date"), -t.get("n_units", 0), -t.get("thai_chars", 0)))
                for t in same[1:]:
                    t["duplicate_of"] = same[0]["file_id"]
        ds = [d for d in ds if not d.get("duplicate_of")]
        taken = set()
        # registry-listed file first, so it keeps the plain id
        for d in sorted(ds, key=lambda d: d["registry_match"] != "folder+file"):
            ed = d.get("edition") or int(ver(d)[1])
            key = (d.get("year_be"), ed)
            # unit ids are <doc>@<year>#<unit>, so (doc, year) must be unique;
            # a second edition in the same year gets "~ฉn"
            yr = d.get("year_be") or "na"
            for new in (rid, f"{rid}~ฉ{ed}", f"{rid}~{d['file_id'][:6]}"):
                if (new, yr) not in taken:
                    break
            taken.add((new, yr))
            old = d.get("doc_id")
            d["doc_id"] = new
            if old:
                remap[old] = new

    with open(work / "docs.jsonl", "w", encoding="utf-8") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    dropped = {d["doc_id"] for d in docs if d.get("duplicate_of") and d.get("doc_id")}
    units = [json.loads(l) for l in open(work / "units.jsonl", encoding="utf-8")]
    units = [u for u in units if u["doc_id"] not in dropped]
    for u in units:
        if u["doc_id"] in remap:
            new = remap[u["doc_id"]]
            u["id"] = new + u["id"][len(u["doc_id"]):]
            u["doc_id"] = new
    with open(work / "units.jsonl", "w", encoding="utf-8") as fh:
        for u in units:
            fh.write(json.dumps(u, ensure_ascii=False) + "\n")

    from collections import Counter
    print("format duplicates:", sum(1 for d in docs if d.get("duplicate_of")),
          "| suffixed ids:", sum(1 for d in docs if "~" in (d.get("doc_id") or "")))
    print("match:", dict(Counter(d["registry_match"] for d in docs)))
    hit = {d["registry_id"] for d in docs if d["registry_id"]}
    inforce = [r for r in reg if r["status"] == "ใช้บังคับ"]
    print(f"registry rows covered: {len(hit & set(reg_by_row))}/{len(reg)}"
          f" (in force {sum(1 for r in inforce if 'T%d' % r['row'] in hit)}/{len(inforce)})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
