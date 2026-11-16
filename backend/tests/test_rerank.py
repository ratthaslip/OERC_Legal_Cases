"""Reranker: อ่านรูปแบบผลของ TEI / Cohere-compatible ถูก, จัดอันดับใหม่จริง, และถ้าโมเดลล่มต้องคงลำดับเดิม"""
import json
from dataclasses import dataclass

import httpx
import pytest

from app.config import get_settings
from app.search import rerank as rr


@dataclass
class H:
    chunk_id: int
    title: str
    chunk_text: str
    kind: str = "provision"
    score: float = 0.0


def hits():
    # hybrid เดิมให้ A สูงสุด แต่ตัวบทที่ตอบคำถามจริงคือ C
    return [H(1, "ข้อ 8", "A ยื่นทางไปรษณีย์", score=0.9), H(2, "ข้อ 10", "B วันประทับตรา", score=0.8),
            H(3, "มาตรา 121", "C ภายในสามสิบวัน", score=0.5)]


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rerank_base_url", "http://reranker")
    monkeypatch.setattr(s, "rerank_model_weight", 0.8)
    monkeypatch.setattr(s, "rerank_candidates", 30)
    return s


def mock_client(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(rr.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def scores_by_text(req_docs):
    return [0.95 if d.endswith("C ภายในสามสิบวัน") else 0.1 for d in req_docs]


def test_tei_reorders(monkeypatch, settings):
    monkeypatch.setattr(settings, "rerank_provider", "tei")

    def handler(req):
        assert req.url.path == "/rerank"
        body = json.loads(req.content)
        sc = scores_by_text(body["texts"])
        return httpx.Response(200, json=sorted([{"index": i, "score": s} for i, s in enumerate(sc)],
                                               key=lambda x: -x["score"]))
    mock_client(monkeypatch, handler)
    out = sorted(rr.rerank("ยื่นอุทธรณ์ภายในกี่วัน", hits(), set(), {}), key=lambda h: -h.score)
    assert out[0].chunk_id == 3


def test_cohere_compatible_reorders(monkeypatch, settings):
    monkeypatch.setattr(settings, "rerank_provider", "cohere")

    def handler(req):
        assert req.url.path == "/v1/rerank"
        body = json.loads(req.content)
        assert body["model"] == settings.rerank_model
        sc = scores_by_text(body["documents"])
        return httpx.Response(200, json={"results": [{"index": i, "relevance_score": s} for i, s in enumerate(sc)]})
    mock_client(monkeypatch, handler)
    out = sorted(rr.rerank("ยื่นอุทธรณ์ภายในกี่วัน", hits(), set(), {}), key=lambda h: -h.score)
    assert out[0].chunk_id == 3


def test_model_down_keeps_hybrid_order(monkeypatch, settings):
    monkeypatch.setattr(settings, "rerank_provider", "tei")
    mock_client(monkeypatch, lambda req: httpx.Response(503))
    out = rr.rerank("ยื่นอุทธรณ์ภายในกี่วัน", hits(), set(), {})
    assert [h.chunk_id for h in sorted(out, key=lambda h: -h.score)] == [1, 2, 3]
    assert [h.score for h in out] == [0.9, 0.8, 0.5]


def test_none_is_noop(monkeypatch, settings):
    monkeypatch.setattr(settings, "rerank_provider", "none")
    out = rr.rerank("x", hits(), set(), {})
    assert [h.score for h in out] == [0.9, 0.8, 0.5]


def test_candidates_outside_head_stay_below(monkeypatch, settings):
    monkeypatch.setattr(settings, "rerank_provider", "tei")
    monkeypatch.setattr(settings, "rerank_candidates", 2)
    mock_client(monkeypatch, lambda req: httpx.Response(200, json=[{"index": 0, "score": 0.2}, {"index": 1, "score": 0.9}]))
    out = sorted(rr.rerank("q", hits(), set(), {}), key=lambda h: -h.score)
    assert [h.chunk_id for h in out] == [2, 1, 3]
