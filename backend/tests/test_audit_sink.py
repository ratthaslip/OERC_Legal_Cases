"""audit log: แก้/ลบไม่ได้ในฐาน, สำเนาออกนอกฐานเฉพาะเหตุการณ์ที่ commit, ตรวจสำเนาเทียบฐานได้"""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.auth import DEMO_USERS


@pytest.fixture()
def sink_file(seeded_client, tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_SINK", f"log,file:{path}")
    return path


def _lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_audit_rows_cannot_be_changed(seeded_client):
    from app import audit
    from app.db import get_engine
    with get_engine().begin() as c:   # trigger ระดับแถวทำงานเมื่อมีแถว
        audit.record(c, DEMO_USERS["adm1"], "test.row", "x/0")
    for stmt in ("UPDATE audit_event SET outcome = 'X'", "DELETE FROM audit_event", "TRUNCATE audit_event"):
        with pytest.raises(DBAPIError, match="append-only"), get_engine().begin() as c:
            c.execute(text(stmt))


def test_only_committed_events_are_shipped(sink_file):
    from app import audit
    from app.db import get_engine
    adm = DEMO_USERS["adm1"]
    with get_engine().begin() as c:
        audit.record(c, adm, "test.committed", "x/1")
    with pytest.raises(RuntimeError), get_engine().begin() as c:
        audit.record(c, adm, "test.rolled_back", "x/2")
        raise RuntimeError("rollback")
    actions = [e["action"] for e in _lines(sink_file)]
    assert "test.committed" in actions and "test.rolled_back" not in actions
    ev = _lines(sink_file)[-1]
    with get_engine().connect() as c:
        row = c.execute(text("SELECT hash, prev_hash FROM audit_event WHERE seq = :s"), {"s": ev["seq"]}).one()
    assert (ev["hash"], ev["prev_hash"]) == tuple(row)


def test_api_events_reach_the_sink_and_verify(seeded_client, sink_file):
    from app.ops import audit_verify
    r = seeded_client.get("/api/v1/court-cases", headers={"X-Demo-User": "lgl1"})
    case_id = r.json()["items"][0]["case_id"]
    seeded_client.get(f"/api/v1/court-cases/{case_id}", headers={"X-Demo-User": "lgl1"})
    assert any(e["action"] == "court_case.view" for e in _lines(sink_file))
    rep = audit_verify(str(sink_file))
    assert rep["ok"] and not rep["mismatched_seq"] and not rep["missing_from_db"]
    # สำเนาที่ถูกแก้ = ตรวจพบ
    tampered = [dict(e, hash="0" * 64) if i == 0 else e for i, e in enumerate(_lines(sink_file))]
    sink_file.write_text("".join(json.dumps(e) + "\n" for e in tampered), encoding="utf-8")
    assert not audit_verify(str(sink_file))["ok"]


def test_ops_check(seeded_client):
    from app.ops import check
    r = check()
    assert r["audit_chain_valid"] and r["counts"]["legal_instrument"] > 0 and r["schema_version"]
