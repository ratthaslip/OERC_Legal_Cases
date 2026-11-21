"""request ID ต่อคำขอ, access log แบบ JSON (ไม่มี query string), ข้อผิดพลาด 500 ตอบรหัสคำขอโดยไม่เปิด stack trace"""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.observability import JsonFormatter


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def test_request_id_generated_and_echoed(client):
    r = client.get("/api/v1/me")
    assert len(r.headers["x-request-id"]) == 32
    r = client.get("/api/v1/me", headers={"X-Request-Id": "waf-abc.123"})
    assert r.headers["x-request-id"] == "waf-abc.123"
    r = client.get("/api/v1/me", headers={"X-Request-Id": "bad id with spaces\n"})
    assert r.headers["x-request-id"] != "bad id with spaces\n" and len(r.headers["x-request-id"]) == 32


def test_access_log_is_structured_without_query(client, caplog):
    caplog.set_level(logging.INFO, logger="erc.access")
    client.get("/api/v1/me?q=ชื่อบุคคล", headers={"X-Request-Id": "rid-1", "X-Demo-User": "lgl1"})
    rec = [r for r in caplog.records if r.name == "erc.access"][-1]
    line = json.loads(JsonFormatter().format(rec))
    assert line["request_id"] == "rid-1" and line["status"] == 200 and line["user"] == "lgl1"
    assert line["path"] == "/api/v1/me" and "ชื่อบุคคล" not in json.dumps(line, ensure_ascii=False)
    assert line["query_len"] > 0 and line["duration_ms"] >= 0


def test_unhandled_error_returns_request_id(client, caplog):
    from app.main import app

    def boom():
        raise RuntimeError("secret internal detail")

    app.add_api_route("/api/v1/_boom_test", boom)
    app.router.routes.insert(0, app.router.routes.pop())   # ก่อน route หน้าเว็บ (catch-all) ถ้ามี

    caplog.set_level(logging.ERROR, logger="erc.error")
    r = client.get("/api/v1/_boom_test", headers={"X-Request-Id": "rid-boom"})
    assert r.status_code == 500 and r.json()["request_id"] == "rid-boom"
    assert "secret internal detail" not in r.text
    err = [x for x in caplog.records if x.name == "erc.error"][-1]
    assert json.loads(JsonFormatter().format(err))["request_id"] == "rid-boom" and err.exc_info
