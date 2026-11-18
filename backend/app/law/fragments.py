"""แยกถ้อยคำมาตรา/ข้อเป็นวรรคและอนุ เพื่ออ้างอิงได้ละเอียดถึงวรรค

รหัสอ้างอิงต่อท้าย ELI ของรุ่น:  eli/th/act/2550/energy-industry/sec-121/v1#para-4
                                 eli/th/act/2550/energy-industry/sec-11/v1#para-1/item-4
กติกา (ตามรูปแบบตัวบทฉบับรวมของสำนักงานคณะกรรมการกฤษฎีกาที่ใช้อยู่):
  - 1 บรรทัด = 1 ย่อหน้า
  - บรรทัดที่ขึ้นต้นด้วย "(1)" / "(๑)" เป็นอนุของวรรคก่อนหน้า, "(ก)" เป็นอนุย่อยของอนุก่อนหน้า
  - บรรทัดธรรมดาที่ตามหลังอนุ = ขึ้นวรรคใหม่
คำนวณจากถ้อยคำทุกครั้ง (ไม่เก็บตาราง) จึงตรงกับตัวบทรุ่นนั้นเสมอ — เป็นกติกาเชิงรูปแบบ ควรให้นิติกรสุ่มตรวจ
"""
import re
from dataclasses import dataclass

from ..search.thai import normalize, tokenize

THAI_ORD = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า", "สิบ",
            "สิบเอ็ด", "สิบสอง", "สิบสาม", "สิบสี่", "สิบห้า"]
_ITEM = re.compile(r"^\((\d+(?:/\d+)?)\)\s*")
_SUB = re.compile(r"^\(([ก-ฮ])\)\s*")


@dataclass
class Fragment:
    id: str          # para-2 | para-1/item-4 | para-1/item-4/sub-ก
    label: str       # วรรคสอง | วรรคหนึ่ง (4) | วรรคหนึ่ง (4) (ก)
    text: str

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "text": self.text}


def para_label(n: int) -> str:
    return "วรรค" + (THAI_ORD[n] if n < len(THAI_ORD) else str(n))


def split(body: str) -> list[Fragment]:
    out: list[Fragment] = []
    para, item = 0, None
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        n = normalize(line)
        m_item, m_sub = _ITEM.match(n), _SUB.match(n)
        if m_item and para:
            item = m_item.group(1)
            out.append(Fragment(f"para-{para}/item-{item}", f"{para_label(para)} ({item})", line))
        elif m_sub and para and item:
            s = m_sub.group(1)
            out.append(Fragment(f"para-{para}/item-{item}/sub-{s}", f"{para_label(para)} ({item}) ({s})", line))
        else:
            para, item = para + 1, None
            out.append(Fragment(f"para-{para}", para_label(para), line))
    return out


def ref(eli: str, version_no: int | None, frag_id: str) -> str:
    return f"{eli}/v{version_no}#{frag_id}" if version_no else f"{eli}#{frag_id}"


def best(body: str, q_terms: set[str]) -> Fragment | None:
    """วรรค/อนุที่ตรงคำถามมากที่สุด — None ถ้ามาตรามีวรรคเดียวหรือไม่มีส่วนใดตรงเลย

    อนุได้ข้อความนำของวรรคแม่มาช่วยให้คะแนน (เช่น "ให้คณะกรรมการมีอำนาจหน้าที่ ดังต่อไปนี้")
    """
    frags = split(body)
    if len(frags) < 2 or not q_terms:
        return None
    heads = {f.id: f.text for f in frags if "/" not in f.id}
    scored = []
    for i, f in enumerate(frags):
        toks = set(tokenize(f.text))
        lead = set(tokenize(heads.get(f.id.split("/")[0], ""))) if "/" in f.id else set()
        hit = len(q_terms & toks) + 0.3 * len(q_terms & (lead - toks))
        scored.append((hit, -i, f))
    top = max(scored, key=lambda x: (x[0], x[1]))
    return top[2] if top[0] > 0 else None


def with_labels(body: str) -> str:
    """ถ้อยคำพร้อมป้ายวรรค สำหรับส่งให้ LLM อ้างได้ถึงวรรค: "[วรรคหนึ่ง] …\n[วรรคสอง] …" """
    frags = split(body)
    if len(frags) < 2:
        return body
    return "\n".join(f"[{f.label}] {f.text}" for f in frags)
