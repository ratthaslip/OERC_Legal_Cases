"""Step 1: pick the best source file per document and extract normalised text.

Preference per document (same folder + same file stem): .docx > .doc > text PDF.
PDFs that are image-only or use broken font encodings are flagged needs_ocr.
"""
import csv, json, pathlib, re, subprocess, sys, tempfile, warnings
from collections import defaultdict

warnings.filterwarnings("ignore")
import pymupdf  # noqa: E402
import docx  # noqa: E402

from .textnorm import normalize, quality

PREF = {".docx": 0, ".doc": 1, ".pdf": 2}


def registry_group(path: str) -> str:
    """'01.1-01.52/1.15.2/x.pdf' -> '1.15.2'; '83/x.pdf' -> '83'; root files -> ''."""
    parts = path.split("/")
    if len(parts) == 1:
        return ""
    if parts[0].startswith("01.1-") and len(parts) > 2:
        return parts[1]
    return parts[0].lstrip("0") or "0"


def doc_key(path: str) -> str:
    p = pathlib.PurePosixPath(path)
    stem = re.sub(r"\s+", " ", p.stem).strip().lower()
    return f"{p.parent}/{stem}"


THAI_NUM = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
EXPLICIT_UNIT = re.compile(r"^\s*(ข้อ|มาตรา)\s*[0-9๐-๙]", re.M)


def _numbered(p):
    """Return list level if the paragraph uses Word auto-numbering, else None."""
    ppr = p._p.pPr
    if ppr is None or ppr.numPr is None or ppr.numPr.ilvl is None:
        return None if ppr is None or ppr.numPr is None else 0
    return int(ppr.numPr.ilvl.val)


def docx_text(path) -> str:
    d = docx.Document(str(path))
    out = [p.text for p in d.paragraphs]
    if not EXPLICIT_UNIT.search("\n".join(out)):
        # Clauses numbered by Word list numbering lose their numbers in the XML text;
        # rebuild them: level 0 -> "ข้อ n", deeper levels -> "(n)".
        out, n0, n1 = [], 0, 0
        for p in d.paragraphs:
            lvl = _numbered(p) if p.text.strip() else None
            if lvl == 0:
                n0 += 1; n1 = 0
                out.append(f"ข้อ {str(n0).translate(THAI_NUM)} {p.text}")
            elif lvl is not None:
                n1 += 1
                out.append(f"({str(n1).translate(THAI_NUM)}) {p.text}")
            else:
                out.append(p.text)
    for t in d.tables:
        for row in t.rows:
            out.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(out)


def doc_to_docx(path, workdir) -> pathlib.Path:
    subprocess.run(["soffice", "--headless", "--convert-to", "docx", "--outdir", str(workdir), str(path)],
                   capture_output=True, timeout=180)
    return pathlib.Path(workdir) / (pathlib.Path(path).stem + ".docx")


def pdf_text(path):
    d = pymupdf.open(str(path))
    return "\n".join(p.get_text() for p in d), len(d)


def pdf_ok(text: str, pages: int) -> bool:
    q = quality(text)
    nonspace = len(re.sub(r"\s", "", text)) or 1
    return (q["thai_chars"] / max(1, pages) >= 100 and q["thai_chars"] / nonspace >= 0.6
            and q["tone_ratio"] >= 0.03)


def main(raw_dir, out_dir):
    raw, out = pathlib.Path(raw_dir), pathlib.Path(out_dir)
    (out / "text").mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(raw / "manifest.csv", encoding="utf-8")))
    groups = defaultdict(list)
    for r in rows:
        groups[doc_key(r["path"])].append(r)
    docs = []
    tmp = pathlib.Path(tempfile.mkdtemp())
    for key, files in sorted(groups.items()):
        files.sort(key=lambda r: PREF.get(r["ext"], 9))
        rec = {"doc_key": key, "group": registry_group(files[0]["path"]),
               "files": [f["path"] for f in files], "source": None, "method": None,
               "needs_ocr": False, "pages": None}
        for f in files:
            src = raw / f"{f['file_id']}{f['ext']}"
            try:
                if f["ext"] == ".docx":
                    text = docx_text(src); method = "docx"
                elif f["ext"] == ".doc":
                    text = docx_text(doc_to_docx(src, tmp)); method = "doc"
                else:
                    text, pages = pdf_text(src); rec["pages"] = pages
                    if not pdf_ok(normalize(text), pages):
                        continue
                    method = "pdf_text"
            except Exception as e:  # corrupt file: try next candidate
                rec.setdefault("errors", []).append(f"{f['path']}: {e}")
                continue
            text = normalize(text)
            if quality(text)["thai_chars"] < 200:
                continue
            rec.update(source=f["path"], file_id=f["file_id"], method=method, **quality(text))
            (out / "text" / f"{f['file_id']}.txt").write_text(text, encoding="utf-8")
            break
        if rec["source"] is None:
            rec["needs_ocr"] = True
            pdfs = [f for f in files if f["ext"] == ".pdf"]
            if pdfs:
                rec["file_id"] = pdfs[0]["file_id"]
        docs.append(rec)
    with open(out / "docs.jsonl", "w", encoding="utf-8") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    n_ok = sum(1 for d in docs if d["source"])
    print(f"documents={len(docs)} extracted={n_ok} needs_ocr={len(docs) - n_ok}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
