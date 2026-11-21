"""Embedding provider

- hash: bag-of-words แบบ hashing (ไม่ต้องมีโมเดล) — เก็บใน search_chunk.embedding เสมอ ใช้เมื่อยังไม่มี index จากโมเดล
- openai_compat (index จากโมเดล, search/vector_index.py): เรียก /v1/embeddings ของ Ollama, vLLM, TEI หรือบริการ cloud ที่รองรับรูปแบบนี้
  แนะนำ bge-m3 (รองรับภาษาไทย) บน GPU ของตัวเอง: `ollama pull bge-m3`
"""
import hashlib
import math
from itertools import pairwise

import httpx

from ..config import get_settings
from .thai import tokenize


def _hash_embed(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    toks = tokenize(text)
    grams = toks + [a + "_" + b for a, b in pairwise(toks)]
    for g in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 63) == 0 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def hash_embed(texts: list[str]) -> list[list[float]]:
    """เวกเตอร์ตั้งต้นใน search_chunk.embedding — คำนวณในเครื่องเสมอ (ไม่ส่งข้อมูลออกนอกระบบ)"""
    dim = get_settings().embed_dim
    return [_hash_embed(t, dim) for t in texts]


class EmbedUnavailable(RuntimeError):
    pass


def model_embed(cfg, texts: list[str], batch: int = 32) -> list[list[float]]:
    """เรียกโมเดล embedding ตามค่าตั้ง (admin/model_config.EmbedConfig) — ผู้เรียกต้องกรองข้อมูลภายในเองถ้า cfg.external"""
    if cfg.provider == "hash":
        return hash_embed(texts)
    if cfg.provider != "openai_compat":
        raise EmbedUnavailable(f"EMBED_PROVIDER ไม่รู้จัก: {cfg.provider}")
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    out: list[list[float]] = []
    try:
        with httpx.Client(timeout=cfg.timeout_s) as client:
            for i in range(0, len(texts), batch):
                r = client.post(f"{cfg.base_url}/embeddings", headers=headers,
                                json={"model": cfg.model, "input": texts[i:i + batch]})
                r.raise_for_status()
                out.extend(_normalize(d["embedding"]) for d in sorted(r.json()["data"], key=lambda d: d["index"]))
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
        raise EmbedUnavailable(f"{type(e).__name__}: {e}"[:300]) from e
    if len(out) != len(texts):
        raise EmbedUnavailable(f"ได้เวกเตอร์ {len(out)} จาก {len(texts)} ข้อความ")
    return out


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]
