"""หน้าตั้งค่าผู้ดูแล: แก้ LLM endpoint / model / API key — ADM เท่านั้น, key เข้ารหัสและไม่ส่งกลับ, บันทึก audit"""
import os

import pytest


@pytest.fixture(scope="module")
def client():
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash")
    os.environ.pop("REGISTER_XLSX", None)
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
    return TestClient(app)


ADM = {"X-Demo-User": "adm1"}


@pytest.fixture(autouse=True)
def clean(client, monkeypatch):
    monkeypatch.delenv("CONFIG_SECRET", raising=False)
    monkeypatch.delenv("LLM_ALLOWED_BASE_URLS", raising=False)
    client.delete("/api/v1/admin/llm", headers=ADM)
    yield
    client.delete("/api/v1/admin/llm", headers=ADM)


def test_only_admin(client):
    for u in ("pub", "stf1", "lgl1", "dir1"):
        assert client.get("/api/v1/admin/llm", headers={"X-Demo-User": u}).status_code == 404
        assert client.put("/api/v1/admin/llm", json={"model": "x"}, headers={"X-Demo-User": u}).status_code == 404
    assert client.get("/api/v1/admin/llm", headers=ADM).status_code == 200


def test_update_endpoint_model_and_external_flag(client):
    r = client.put("/api/v1/admin/llm", json={"provider": "openai_compat", "base_url": "https://api.openai.com/v1/",
                                              "model": "gpt-test", "timeout_s": 45}, headers=ADM)
    v = r.json()
    assert r.status_code == 200 and v["base_url"] == "https://api.openai.com/v1" and v["model"] == "gpt-test"
    assert v["external"] is True and v["source"]["model"] == "db" and v["updated_by"] == "adm1"
    st = client.get("/api/v1/assistant/status").json()
    assert st["provider"] == "openai_compat" and st["external"] is True  # มีผลทันทีโดยไม่ต้อง restart
    v = client.put("/api/v1/admin/llm", json={"base_url": "http://host.docker.internal:11434/v1"}, headers=ADM).json()
    assert v["external"] is False


def test_validation(client, monkeypatch):
    bad = [{"provider": "magic"}, {"base_url": "ftp://x"}, {"model": "a b;rm"}, {"timeout_s": 1}]
    for b in bad:
        assert client.put("/api/v1/admin/llm", json=b, headers=ADM).status_code == 400
    monkeypatch.setenv("LLM_ALLOWED_BASE_URLS", "https://api.openai.com/v1, http://vllm:8000/v1")
    assert client.put("/api/v1/admin/llm", json={"base_url": "https://evil.example/v1"}, headers=ADM).status_code == 400
    assert client.put("/api/v1/admin/llm", json={"base_url": "http://vllm:8000/v1"}, headers=ADM).status_code == 200


def test_api_key_needs_secret_is_encrypted_and_never_returned(client, monkeypatch):
    from sqlalchemy import text

    from app.admin import llm_config
    from app.db import get_engine
    key = "sk-test-0123456789abcdef"
    r = client.put("/api/v1/admin/llm", json={"api_key": key}, headers=ADM)
    assert r.status_code == 400 and "CONFIG_SECRET" in r.text
    monkeypatch.setenv("CONFIG_SECRET", "unit-test-secret")
    r = client.put("/api/v1/admin/llm", json={"api_key": key}, headers=ADM)
    assert r.status_code == 200 and key not in r.text and r.json()["key_hint"] == "…cdef"
    assert key not in client.get("/api/v1/admin/llm", headers=ADM).text
    with get_engine().connect() as c:
        stored = c.execute(text("SELECT value, secret FROM app_setting WHERE key = 'llm.api_key'")).one()
        trail = c.execute(text("SELECT detail::text FROM audit_event WHERE action LIKE 'admin.llm%'")).scalars().all()
    assert stored.secret and key not in stored.value
    assert all(key not in t for t in trail)
    assert llm_config.effective().api_key == key
    monkeypatch.setenv("CONFIG_SECRET", "rotated")          # เปลี่ยน secret → ถอดไม่ได้ → กลับไปใช้ .env (ไม่ล่ม)
    llm_config.invalidate()
    assert llm_config.effective().api_key != key
    monkeypatch.setenv("CONFIG_SECRET", "unit-test-secret")
    assert client.put("/api/v1/admin/llm", json={"api_key": ""}, headers=ADM).json()["source"]["api_key"] == "env"


def test_connection_test_and_reset(client):
    r = client.post("/api/v1/admin/llm/test", headers=ADM).json()
    assert r["ok"] and r["provider"] == "extractive"
    client.put("/api/v1/admin/llm", json={"model": "m2"}, headers=ADM)
    v = client.delete("/api/v1/admin/llm", headers=ADM).json()
    assert v["source"]["model"] == "env"
