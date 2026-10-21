"""Embedding provider

- hash: bag-of-words แบบ hashing (ไม่ต้องมีโมเดล) — ใช้ตอนพัฒนาและเทสต์ ความหมายเชิงลึกจะไม่ดีเท่าโมเดลจริง
- openai_compat: เรียก /v1/embeddings ของ Ollama, vLLM, TEI หรือบริการ cloud ที่รองรับรูปแบบนี้
  แนะนำ bge-m3 (รองรับภาษาไทย) บน GPU ของตัวเอง: `ollama pull bge-m3`
"""
import hashlib
import math

import httpx

from ..config import get_settings
from .thai import tokenize


def _hash_embed(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    toks = tokenize(text)
    grams = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
    for g in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 63) == 0 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if s.embed_provider == "hash":
        return [_hash_embed(t, s.embed_dim) for t in texts]
    if s.embed_provider == "openai_compat":
        headers = {"Authorization": f"Bearer {s.embed_api_key}"} if s.embed_api_key else {}
        out: list[list[float]] = []
        with httpx.Client(timeout=120) as client:
            for i in range(0, len(texts), 32):
                r = client.post(f"{s.embed_base_url}/embeddings", headers=headers,
                                json={"model": s.embed_model, "input": texts[i:i + 32]})
                r.raise_for_status()
                out.extend(_normalize(d["embedding"]) for d in sorted(r.json()["data"], key=lambda d: d["index"]))
        return out
    raise ValueError(f"EMBED_PROVIDER ไม่รู้จัก: {s.embed_provider}")


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]
