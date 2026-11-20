"""หน้าตั้งค่า reranker / embedding และ embedding index

- ADM เท่านั้น; ค่าที่บันทึกมีผลทันที; ตรวจภายนอก/ภายในจาก URL
- reranker ภายนอก: ส่งให้โมเดลเฉพาะตัวบทสาธารณะ ที่เหลือคงคะแนนเดิม
- embedding index ภายนอก: สร้างเวกเตอร์เฉพาะตัวบทสาธารณะ; ค้นด้วย pgvector และ numpy ได้ผลเดียวกัน;
  โมเดลล่มตอนค้น → ถอยไปใช้ hash ไม่ error
"""
import json
from dataclasses import dataclass

import httpx
import pytest

ADM = {"X-Demo-User": "adm1"}


@pytest.fixture(scope="module")
def client(seeded_client):
    return seeded_client


@pytest.fixture(autouse=True)
def clean(client, monkeypatch):
    for v in ("CONFIG_SECRET", "RERANK_ALLOWED_BASE_URLS", "EMBED_ALLOWED_BASE_URLS", "RERANK_EXTERNAL", "EMBED_EXTERNAL"):
        monkeypatch.delenv(v, raising=False)
    yield
    client.delete("/api/v1/admin/rerank", headers=ADM)
    client.delete("/api/v1/admin/embed", headers=ADM)
    client.post("/api/v1/admin/embed/deactivate", headers=ADM)
    for i in client.get("/api/v1/admin/embed", headers=ADM).json()["indexes"]:
        client.delete(f"/api/v1/admin/embed/indexes/{i['index_id']}", headers=ADM)


def test_only_admin(client):
    for path in ("/api/v1/admin/rerank", "/api/v1/admin/embed"):
        for u in ("pub", "stf1", "lgl1", "dir1"):
            assert client.get(path, headers={"X-Demo-User": u}).status_code == 404
            assert client.put(path, json={"model": "x"}, headers={"X-Demo-User": u}).status_code == 404
        assert client.get(path, headers=ADM).status_code == 200
    assert client.post("/api/v1/admin/embed/indexes", json={}, headers={"X-Demo-User": "lgl1"}).status_code == 404


def test_rerank_update_and_validation(client):
    v = client.put("/api/v1/admin/rerank", json={"provider": "cohere", "base_url": "https://api.cohere.com/",
                                                 "model": "rerank-v3.5", "candidates": 20, "model_weight": 0.6},
                   headers=ADM).json()
    assert v["provider"] == "cohere" and v["base_url"] == "https://api.cohere.com" and v["external"] is True
    assert v["candidates"] == 20 and v["model_weight"] == 0.6 and v["source"]["candidates"] == "db"
    v = client.put("/api/v1/admin/rerank", json={"provider": "tei", "base_url": "http://reranker:80"}, headers=ADM).json()
    assert v["external"] is False
    for bad in ({"provider": "magic"}, {"candidates": 1000}, {"model_weight": 2}, {"base_url": "ftp://x"}):
        assert client.put("/api/v1/admin/rerank", json=bad, headers=ADM).status_code == 400
    assert client.post("/api/v1/admin/rerank/test", headers=ADM).json()["ok"] is False  # ไม่มี reranker ในเทสต์
    v = client.delete("/api/v1/admin/rerank", headers=ADM).json()
    assert v["source"]["provider"] == "env"


def test_rerank_key_needs_secret_and_is_hidden(client, monkeypatch):
    assert client.put("/api/v1/admin/rerank", json={"api_key": "co-secret-1234"}, headers=ADM).status_code == 400
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CONFIG_SECRET", Fernet.generate_key().decode())
    r = client.put("/api/v1/admin/rerank", json={"api_key": "co-secret-1234"}, headers=ADM)
    assert r.status_code == 200 and "co-secret-1234" not in r.text and r.json()["key_hint"] == "…1234"


# ---------------------------------------------------------------- reranker guard (unit)
@dataclass
class H:
    chunk_id: int
    title: str
    chunk_text: str
    kind: str = "provision"
    classification: str = "public"
    is_demo: bool = False
    score: float = 0.0


def _cfg(external: bool):
    from app.admin.model_config import RerankConfig
    return RerankConfig("tei", "https://rerank.example.com", "m", "", 30, 1.0, 5, external, {})


def _mock(monkeypatch, sent):
    from app.search import rerank as rr
    real = httpx.Client

    def handler(req):
        texts = json.loads(req.content)["texts"]
        sent.extend(texts)
        return httpx.Response(200, json=[{"index": i, "score": 1.0 - i * 0.1} for i in range(len(texts))])
    monkeypatch.setattr(rr.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _hits():
    return [H(1, "ทะเบียน 1.2", "แถวทะเบียน", kind="register", score=0.9),
            H(2, "ข้อ 3 ระเบียบบุคคล", "ระเบียบภายใน", classification="internal", score=0.8),
            H(3, "อธ. 1/2567", "สำนวน", kind="appeal", classification="restricted", score=0.7),
            H(4, "มาตรา 121", "ตัวบทสาธารณะ", score=0.5)]


def test_external_reranker_sees_only_public_law(monkeypatch):
    from app.admin import model_config
    from app.search import rerank as rr
    sent: list[str] = []
    _mock(monkeypatch, sent)
    monkeypatch.setattr(model_config, "rerank_effective", lambda conn=None: _cfg(True))
    out = {h.chunk_id: h.score for h in rr.rerank("q", _hits(), set(), {})}
    assert sent == ["มาตรา 121\nตัวบทสาธารณะ"]
    # ที่ไม่ได้ส่ง: คะแนนเดิม (สัดส่วนต่อคะแนนสูงสุด) คงอยู่
    assert out[1] == pytest.approx(0.9) and out[2] == pytest.approx(0.8)


def test_internal_reranker_sees_everything(monkeypatch):
    from app.admin import model_config
    from app.search import rerank as rr
    sent: list[str] = []
    _mock(monkeypatch, sent)
    monkeypatch.setattr(model_config, "rerank_effective", lambda conn=None: _cfg(False))
    rr.rerank("q", _hits(), set(), {})
    assert len(sent) == 4


# ---------------------------------------------------------------- embedding index
def _fake_model(monkeypatch, calls, fail=False):
    """แทนบริการ embedding: hash 64 มิติ (ต่างจาก search_chunk ที่ใช้ 256) + เก็บข้อความที่ถูกส่ง"""
    from app.search import embeddings, vector_index

    def fake(cfg, texts, batch=32):
        if fail:
            raise embeddings.EmbedUnavailable("down")
        calls.extend(texts)
        return [embeddings._hash_embed(t, 64) for t in texts]
    monkeypatch.setattr(vector_index, "model_embed", fake)
    monkeypatch.setattr(vector_index, "build_in_background", lambda i: vector_index.build(i))


def _make_index(client):
    v = client.put("/api/v1/admin/embed", json={"provider": "openai_compat", "base_url": "https://api.openai.com/v1",
                                                "model": "text-embedding-3-small"}, headers=ADM).json()
    assert v["external"] is True
    v = client.post("/api/v1/admin/embed/indexes", json={"activate_when_ready": True}, headers=ADM).json()
    return client.get("/api/v1/admin/embed", headers=ADM).json()


def test_external_index_embeds_only_public_law(client, monkeypatch):
    calls: list[str] = []
    _fake_model(monkeypatch, calls)
    v = _make_index(client)
    idx = v["indexes"][0]
    assert idx["status"] == "ready" and idx["active"] and idx["dim"] == 64 and v["active_index"] == idx["index_id"]
    cov = idx["coverage"]
    assert cov["missing"] == 0 and cov["withheld"] > 0 and cov["embedded"] == cov["eligible"]
    from sqlalchemy import text

    from app.db import get_engine
    with get_engine().connect() as c:
        leaked = c.execute(text("""
            SELECT count(*) FROM embedding_vec v WHERE v.index_id = :i AND NOT EXISTS (
              SELECT 1 FROM search_chunk s WHERE s.content_sha = v.content_sha
                AND s.kind = 'provision' AND s.classification = 'public' AND NOT s.is_demo)"""),
            {"i": idx["index_id"]}).scalar()
        internal_titles = [r[0] for r in c.execute(text(
            "SELECT title FROM search_chunk WHERE classification <> 'public' OR kind <> 'provision' LIMIT 50"))]
    assert leaked == 0
    sent = "\n".join(calls)
    assert not any(t in sent for t in internal_titles if len(t) > 12)
    # อัปเดตซ้ำไม่มีอะไรต้องส่งเพิ่ม (เวกเตอร์ผูกกับ content_sha)
    calls.clear()
    client.post(f"/api/v1/admin/embed/indexes/{idx['index_id']}/update", headers=ADM)
    assert calls == []


def test_search_uses_active_index_pgvector_and_numpy(client, monkeypatch):
    calls: list[str] = []
    _fake_model(monkeypatch, calls)
    _make_index(client)
    from app.search import vector_index
    q = "ระยะเวลายื่นอุทธรณ์คำสั่งเพิกถอนใบอนุญาต"
    calls.clear()
    a = client.get("/api/v1/laws/search", params={"q": q}, headers={"X-Demo-User": "lgl1"})
    assert a.status_code == 200 and calls == [vector_index_q(q)]  # ฝังคำค้นด้วยโมเดลของ index
    assert client.get("/api/v1/admin/embed", headers=ADM).json()["pgvector"] is True
    monkeypatch.setattr(vector_index, "has_pgvector", lambda conn: False)
    b = client.get("/api/v1/laws/search", params={"q": q}, headers={"X-Demo-User": "lgl1"})
    ids = lambda r: [i.get("eli_id") for i in r.json()["items"][:5]]  # noqa: E731
    assert ids(a) == ids(b)


def vector_index_q(q):
    from app.search.thai import expand_query
    return expand_query(q)


def test_model_down_at_query_falls_back_to_hash(client, monkeypatch):
    calls: list[str] = []
    _fake_model(monkeypatch, calls)
    _make_index(client)
    _fake_model(monkeypatch, calls, fail=True)
    r = client.get("/api/v1/laws/search", params={"q": "อุทธรณ์"}, headers={"X-Demo-User": "lgl1"})
    assert r.status_code == 200 and r.json()["items"]


def test_failed_build_reports_error_and_keeps_old(client, monkeypatch):
    _fake_model(monkeypatch, [], fail=True)
    client.put("/api/v1/admin/embed", json={"provider": "openai_compat", "base_url": "http://ollama:11434/v1",
                                           "model": "bge-m3"}, headers=ADM)
    v = client.post("/api/v1/admin/embed/indexes", json={}, headers=ADM).json()
    v = client.get("/api/v1/admin/embed", headers=ADM).json()
    idx = v["indexes"][0]
    assert idx["status"] == "failed" and "down" in idx["error"] and not idx["active"] and idx["external"] is False
    assert client.post(f"/api/v1/admin/embed/indexes/{idx['index_id']}/activate", headers=ADM).status_code == 400


def test_hash_provider_cannot_create_index(client):
    assert client.post("/api/v1/admin/embed/indexes", json={}, headers=ADM).status_code == 400
