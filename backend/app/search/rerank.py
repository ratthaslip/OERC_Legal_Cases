"""Reranker: จัดอันดับผลค้นชุดแรกใหม่ก่อนส่งให้ผู้ช่วย AI / หน้าค้นหา

ผู้ให้บริการ (RERANK_PROVIDER):
  none   — ไม่ rerank ใช้คะแนน hybrid เดิม (ค่าเริ่มต้น)
  legal  — ทดลอง: คะแนนจากโครงสร้างตัวบท ไม่ต้องใช้โมเดล
           ผลกับชุดทดสอบ 60 ข้อ (16 พ.ย. 2569) ไม่ดีกว่า none จึงไม่เปิดเป็นค่าเริ่มต้น
  tei    — cross-encoder ผ่าน Hugging Face Text Embeddings Inference: POST {base}/rerank
  cohere — cross-encoder ผ่าน API แบบ Cohere/Jina: POST {base}/v1/rerank (vLLM, Infinity, llama.cpp)
           โมเดลที่แนะนำ: BAAI/bge-reranker-v2-m3 (หลายภาษา รวมภาษาไทย, Apache-2.0)

cross-encoder: คะแนนสุดท้าย = w × คะแนนโมเดล + (1−w) × คะแนน hybrid เดิม (w = RERANK_MODEL_WEIGHT)
ถ้าเรียกโมเดลไม่ได้จะคงลำดับ hybrid เดิม — ผู้ช่วย AI ไม่ล่มเพราะ reranker ล่ม

ฟีเจอร์ของ legal (ออกแบบจากข้อที่ไม่ผ่านในชุดทดสอบ):
  body_cov  — คำสำคัญของคำถามที่พบใน "เนื้อ" มาตรา (ไม่นับชื่อกฎหมาย) เพราะทุกข้อในฉบับเดียวกันมีชื่อเหมือนกัน
              คะแนนเดิมที่นับชื่อด้วยจึงแยกข้อข้างเคียงไม่ออก
  title_cov — คำถามระบุชื่อกฎหมายฉบับใด ให้ฉบับนั้นได้เปรียบ
  intent    — คำถามเชิงบรรทัดฐาน (ต้อง/กี่วัน/ได้หรือไม่) ให้ตัวบทมาก่อนสำนวน เว้นแต่ถามถึงสำนวน;
              คำถามนิยามให้บทนิยามของคำนั้น; คำถามระยะเวลา/จำนวนเงินให้ข้อที่มีระยะเวลา/จำนวนเงิน
"""
import logging
import re
from dataclasses import dataclass

import httpx

from ..config import get_settings
from .thai import normalize, tokenize

log = logging.getLogger("erc.rerank")

CASE_KINDS = {"appeal", "court_case"}
ASKS_CASE = re.compile(r"อธ\.?|คดี|สำนวน|ผลการวินิจฉัย|ผลการพิจารณา|ตัวอย่าง|เคยมี|เรื่องอุทธรณ์|ผู้อุทธรณ์ราย|แนวคำวินิจฉัย|ศาล")
NORMATIVE = re.compile(r"กี่วัน|กี่ปี|กี่เดือน|ภายใน|ต้อง|ได้หรือไม่|หรือไม่|อย่างไร|เมื่อใด|เท่าไร|หมายถึง|หมายความ|ใคร|อะไรบ้าง")
DEFINITION_Q = re.compile(r"หมายถึง|หมายความว่า|คืออะไร|นิยาม|คำว่า")
DURATION_Q = re.compile(r"กี่วัน|กี่ปี|กี่เดือน|ระยะเวลา|เมื่อใด|กำหนดเวลา")
AMOUNT_Q = re.compile(r"เท่าไร|กี่บาท|ค่าธรรมเนียม|ค่าปรับ|อัตรา")
DURATION_TXT = re.compile(r"(ภายใน|ไม่เกิน|ไม่น้อยกว่า|นับแต่)[^\n]{0,40}?(วัน|ปี|เดือน)")
AMOUNT_TXT = re.compile(r"บาท")
# ข้อของฉบับแก้ไขที่แทรก/แทนถ้อยคำ — ถ้อยคำซ้ำกับตัวบทฉบับรวม (consolidated) ซึ่งเป็นแหล่งที่ควรอ้าง
AMENDING_TXT = re.compile(r"ให้เพิ่มความต่อไปนี้|ให้ยกเลิกความใน[^\n]{0,80}และให้ใช้ความต่อไปนี้แทน|ให้เพิ่ม[^\n]{0,40}เป็น(ข้อ|มาตรา)")
TERM_AFTER = re.compile(r"(?:คำว่า|“|\")\s*([^\s“”\"]+)")


@dataclass
class Features:
    body_cov: float
    title_cov: float
    boost: float


def _cov(terms: set[str], idf: dict[str, float], toks: set[str]) -> float:
    tot = sum(idf.get(t, 1.0) for t in terms) or 1.0
    return sum(idf.get(t, 1.0) for t in terms if t in toks) / tot


def _definition_term(q: str) -> str | None:
    m = TERM_AFTER.search(q)
    if m:
        return m.group(1)
    m = re.search(r"([^\s]+)\s*(?:หมายถึง|หมายความว่า|คืออะไร)", q)
    return m.group(1) if m else None


def legal_features(q: str, terms: set[str], idf: dict[str, float], h, exact_ref: bool) -> Features:
    body = normalize(h.chunk_text)
    title_toks = set(tokenize(h.title))
    body_toks = set(tokenize(body))
    boost = 0.0
    asks_case = bool(ASKS_CASE.search(q)) or exact_ref
    if NORMATIVE.search(q) and not asks_case:
        boost += 0.08 if h.kind in ("provision", "register") else -0.15
    if DEFINITION_Q.search(q):
        term = _definition_term(normalize(q))
        if term and re.search(rf"[“\"]{re.escape(term)}[”\"]\s*หมายความว่า", body):
            boost += 0.3
        elif "หมายความว่า" in body:
            boost += 0.03
    if h.kind == "provision" and AMENDING_TXT.search(body):
        boost -= 0.1
    if DURATION_Q.search(q) and DURATION_TXT.search(body):
        boost += 0.08
    if AMOUNT_Q.search(q) and AMOUNT_TXT.search(body):
        boost += 0.08
    return Features(_cov(terms, idf, body_toks), _cov(terms, idf, title_toks), boost)


def _model_scores(q: str, docs: list[str]) -> list[float] | None:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.rerank_api_key}"} if s.rerank_api_key else {}
    try:
        with httpx.Client(timeout=s.rerank_timeout_s) as c:
            if s.rerank_provider == "tei":
                r = c.post(f"{s.rerank_base_url.rstrip('/')}/rerank", headers=headers,
                           json={"query": q, "texts": docs, "truncate": True, "raw_scores": False})
                r.raise_for_status()
                out = [0.0] * len(docs)
                for item in r.json():
                    out[item["index"]] = float(item["score"])
                return out
            r = c.post(f"{s.rerank_base_url.rstrip('/')}/v1/rerank", headers=headers,
                       json={"model": s.rerank_model, "query": q, "documents": docs, "top_n": len(docs)})
            r.raise_for_status()
            out = [0.0] * len(docs)
            for item in r.json()["results"]:
                out[item["index"]] = float(item.get("relevance_score", item.get("score", 0.0)))
            return out
    except Exception as e:  # noqa: BLE001 — reranker ล่มต้องไม่ทำให้การค้นล่ม
        log.warning("reranker unavailable, keeping hybrid order: %s", e)
        return None


def rerank(q: str, hits: list, terms: set[str], idf: dict[str, float], exact_ref_of=lambda h: False) -> list:
    """ให้คะแนนใหม่ (h.score) กับผู้สมัคร RERANK_CANDIDATES อันดับแรก; ที่เหลือคงคะแนนเดิมแต่อยู่ท้าย"""
    s = get_settings()
    if s.rerank_provider == "none" or not hits:
        return hits
    hits = sorted(hits, key=lambda h: -h.score)
    head, tail = hits[: s.rerank_candidates], hits[s.rerank_candidates:]
    top_prior = max(h.score for h in head) or 1.0
    prior = [h.score / top_prior for h in head]
    if s.rerank_provider == "legal":
        final = []
        for h, p in zip(head, prior):
            f = legal_features(q, terms, idf, h, exact_ref_of(h))
            final.append(0.4 * f.body_cov + 0.2 * f.title_cov + 0.4 * p + f.boost)
    else:
        ms = _model_scores(q, [f"{h.title}\n{h.chunk_text}"[:2000] for h in head])
        if ms is None:
            return hits
        lo, hi = min(ms), max(ms)
        norm = [(m - lo) / (hi - lo) if hi > lo else 0.5 for m in ms]
        w = s.rerank_model_weight
        final = [(w * n + (1 - w) * p) * top_prior for n, p in zip(norm, prior)]
    floor = min(final) if final else 0.0
    for h, sc in zip(head, final):
        h.score = sc
    for h in tail:  # คงลำดับเดิมแต่อยู่ใต้กลุ่มที่ rerank แล้ว
        h.score = min(h.score, floor) * 0.99
    return head + tail
