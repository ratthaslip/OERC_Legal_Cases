"""LLM provider (SDD 6.4) — คืนข้อความแบบ streaming เป็น generator ของ text delta

- openai_compat: Ollama / vLLM บน GPU ของตัวเอง (ใช้กับข้อมูลจริงได้ เพราะไม่ออกนอกเครื่อง)
- anthropic: cloud API — ใช้กับข้อมูลสมมติใน POC เท่านั้น
- extractive: ไม่เรียกโมเดล ประกอบคำตอบจากข้อความในแหล่งโดยตรง (สาธิต/เทสต์แบบออฟไลน์)
"""
import json
from typing import Iterator

import httpx

from ..config import get_settings


class LlmUnavailable(Exception):
    pass


def stream_chat(system: str, user: str) -> Iterator[str]:
    s = get_settings()
    try:
        if s.llm_provider == "openai_compat":
            yield from _openai_compat(system, user)
        elif s.llm_provider == "anthropic":
            yield from _anthropic(system, user)
        else:
            raise ValueError(f"LLM_PROVIDER ไม่รู้จัก: {s.llm_provider}")
    except httpx.HTTPStatusError as e:
        body = e.response.read().decode(errors="replace")[:300] if e.response is not None else ""
        raise LlmUnavailable(f"HTTP {e.response.status_code}: {body}") from e
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        # ValueError ครอบ json.JSONDecodeError และ LLM_PROVIDER ที่สะกดผิด
        raise LlmUnavailable(f"{type(e).__name__}: {e}") from e


def _openai_compat(system: str, user: str) -> Iterator[str]:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
    body = {"model": s.llm_model, "stream": True, "temperature": 0.1,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    with httpx.stream("POST", f"{s.llm_base_url}/chat/completions", json=body, headers=headers,
                      timeout=s.llm_timeout_s) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            delta = json.loads(line[6:])["choices"][0].get("delta", {}).get("content")
            if delta:
                yield delta


def _anthropic(system: str, user: str) -> Iterator[str]:
    s = get_settings()
    headers = {"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    body = {"model": s.llm_model, "max_tokens": 1500, "temperature": 0.1, "stream": True, "system": system,
            "messages": [{"role": "user", "content": user}]}
    base = s.llm_base_url if "anthropic" in s.llm_base_url else "https://api.anthropic.com"
    with httpx.stream("POST", f"{base}/v1/messages", json=body, headers=headers, timeout=s.llm_timeout_s) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                yield ev["delta"]["text"]
