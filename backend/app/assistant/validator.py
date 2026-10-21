"""Citation validator (SDD 6.2 ขั้น 7) — ตรวจทีละประโยคก่อนส่งถึงผู้ใช้

ประโยคผ่านเมื่อ
1. มีการอ้าง [S#] อย่างน้อย 1 รายการ และทุก S# อยู่ในชุดแหล่งที่ส่งให้โมเดล
2. เลขมาตรา/ข้อ/เลขเรื่องที่ประโยคพูดถึง ต้องปรากฏในแหล่งที่ประโยคนั้นอ้างอย่างน้อยหนึ่งแหล่ง
"""
import re
from dataclasses import dataclass

from ..search.thai import normalize, normalize_ref_no

CITE_RE = re.compile(r"\[(S\d+)\]")
SECTION_RE = re.compile(r"(มาตรา|ข้อ)\s*(\d+)")
REF_RE = re.compile(r"(อธ|รร|คม|พ|อ)\.?\s*\d{1,4}\s*/\s*\d{4}")


@dataclass
class Checked:
    text: str           # ประโยคที่ตัดเครื่องหมายอ้างอิงออกแล้ว
    citations: list[str]
    ok: bool
    reason: str | None = None


def check_sentence(line: str, sources: dict[str, str]) -> Checked:
    """sources: key → ข้อความเต็มของแหล่ง (title + text)"""
    line = normalize(line).strip().lstrip("-*•· ").strip()
    keys = list(dict.fromkeys(CITE_RE.findall(line)))
    clean = CITE_RE.sub("", line).strip()
    if not clean:
        return Checked(clean, keys, False, "empty")
    if not keys:
        return Checked(clean, keys, False, "no_citation")
    if any(k not in sources for k in keys):
        return Checked(clean, keys, False, "unknown_source")
    cited_text = normalize(" ".join(sources[k] for k in keys))
    for kind, num in SECTION_RE.findall(clean):
        if not re.search(rf"{kind}\s*{num}(?!\d)", cited_text):
            return Checked(clean, keys, False, f"{kind} {num} ไม่อยู่ในแหล่งที่อ้าง")
    cited_refs = {normalize_ref_no(m.group(0)) for m in REF_RE.finditer(cited_text)}
    for m in REF_RE.finditer(clean):
        if normalize_ref_no(m.group(0)) not in cited_refs:
            return Checked(clean, keys, False, f"{m.group(0)} ไม่อยู่ในแหล่งที่อ้าง")
    return Checked(clean, keys, True)


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """ตัดเป็นบรรทัดที่จบแล้ว คืน (บรรทัดที่สมบูรณ์, ส่วนที่ยังค้าง)"""
    parts = buffer.split("\n")
    return [p for p in parts[:-1] if p.strip()], parts[-1]
