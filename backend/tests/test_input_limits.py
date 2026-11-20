"""ค่าพารามิเตอร์ที่ผิดรูปต้องได้ 400 (ไม่ใช่ 500) และ CORS ไม่เปิดกว้าง"""
import pytest

from app.config import Q_MAX


@pytest.fixture(scope="module")
def client(seeded_client):
    return seeded_client


def test_negative_offset_and_limit_are_rejected(client):
    assert client.get("/api/v1/laws/register", params={"offset": -1}).status_code == 400
    assert client.get("/api/v1/laws/register", params={"limit": 0}).status_code == 400
    assert client.get("/api/v1/audit-events", params={"limit": -1}, headers={"X-Demo-User": "adm1"}).status_code == 400


def test_overlong_query_is_rejected(client):
    long_q = "ก" * (Q_MAX + 1)
    assert client.get("/api/v1/laws/search", params={"q": long_q}).status_code == 400
    assert client.get("/api/v1/laws/register", params={"q": long_q}).status_code == 400


def test_cors_does_not_allow_arbitrary_headers(client):
    r = client.options("/api/v1/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
                                                  "Access-Control-Request-Headers": "X-Evil"})
    assert "x-evil" not in r.headers.get("access-control-allow-headers", "").lower()
