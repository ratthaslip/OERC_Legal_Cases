"""เทสต์ผ่าน API กับ PostgreSQL จริง (ข้ามถ้าไม่ได้ตั้ง TEST_DATABASE_URL)

ตัวอย่าง: TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/erc_test pytest
⚠ เทสต์จะล้างและ seed ฐานข้อมูลนั้นใหม่ทั้งหมด อย่าชี้ไปที่ฐานที่ใช้งานจริง
"""
import json
import os

import pytest

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="ต้องตั้ง TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def client():
    os.environ["DATABASE_URL"] = TEST_DB
    os.environ["LLM_PROVIDER"] = "extractive"
    os.environ["EMBED_PROVIDER"] = "hash"
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
    return TestClient(app)


def h(user):
    return {"X-Demo-User": user}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name[7:], json.loads(data[6:])))
    return out


def test_point_in_time_by_date(client):
    url = "/api/v1/laws/provisions/eli/th/act/2550/sec-75"
    assert client.get(url, params={"as_of": "2019-05-15"}).json()["resolved_version_no"] == 1
    assert client.get(url, params={"as_of": "2024-03-14"}).json()["resolved_version_no"] == 2
    assert client.get(url, params={"as_of": "2000-01-01"}).json()["problem"] == "NOT_YET_IN_FORCE"


def test_public_user_has_no_case_access(client):
    assert client.get("/api/v1/appeals", headers=h("pub")).status_code == 404
    assert client.get("/api/v1/court-cases", headers=h("pub")).status_code == 404


def _restricted_appeal_id(client):
    items = client.get("/api/v1/appeals", headers=h("lgl1")).json()["items"]
    return next(a["appeal_id"] for a in items if a["classification"] == "restricted")


def test_restricted_appeal_hidden_from_non_owner(client):
    aid = _restricted_appeal_id(client)
    assert client.get(f"/api/v1/appeals/{aid}", headers=h("lgl1")).status_code == 200
    # ผู้ไม่มีสิทธิ์ได้ 404 เหมือนไม่มีเรื่อง (ไม่ใช่ 403)
    assert client.get(f"/api/v1/appeals/{aid}", headers=h("stf1")).status_code == 404
    listed = [a["appeal_id"] for a in client.get("/api/v1/appeals", headers=h("stf1")).json()["items"]]
    assert aid not in listed


def test_restricted_never_in_search_for_non_owner(client):
    items = client.get("/api/v1/appeals", params={"q": "ท่อส่งก๊าซ ความดัน"}, headers=h("stf1")).json()
    assert all(a["classification"] == "internal" for a in items["items"])
    assert items["restricted_omitted"] >= 1


def _do(client, user, aid, path, body, ver, expect=200):
    r = client.post(f"/api/v1/appeals/{aid}{path}", json=body, headers={**h(user), "If-Match": str(ver)})
    assert r.status_code == expect, r.text
    return r.json()


def test_full_appeal_flow_per_work_instruction(client):
    """รับเรื่อง → มอบหมาย → ตรวจ → ขอข้อเท็จจริง → อนุฯ (ขยายเวลา) → วาระ → มติ → แจ้งผล → ปิดเรื่อง"""
    from datetime import date, timedelta
    today = date.today()
    r = client.post("/api/v1/appeals", headers=h("stf1"), json={
        "case_type": "APPEAL", "target_type": "ORDER", "filing_channel": "ELECTRONIC", "esarabun_no": "ES-TEST/1",
        "received_date": (today - timedelta(days=60)).isoformat(), "notice_date": (today - timedelta(days=75)).isoformat(),
        "subject": "อุทธรณ์คำสั่งทดสอบ workflow", "party_code": "T-001", "energy_sector": "กิจการไฟฟ้า"})
    assert r.status_code == 201
    aid = r.json()["appeal_id"]
    d = client.get(f"/api/v1/appeals/{aid}", headers=h("dir1")).json()
    assert [s["state"] for s in d["steps"]][:2] == ["current", "pending"]
    assert next(c for c in d["clocks"] if c["key"] == "filing")["state"] == "DONE_ON_TIME"
    v = d["appeal"]["row_version"]

    # มอบหมายได้เฉพาะ ผอ.คบ.
    _do(client, "lgl1", aid, ":assign", {"officer": "lgl1"}, v, 403)
    d = _do(client, "dir1", aid, ":assign", {"officer": "lgl1"}, v)
    v = d["appeal"]["row_version"]
    # ผลตรวจขัดกับข้อเท็จจริง (เอกสารไม่ครบแต่เลือกรับ) → ปฏิเสธ
    bad = {"checks": {"standing": True, "jurisdiction": True, "docs": {"date": True}}, "result": "ACCEPTED"}
    _do(client, "lgl1", aid, "/steps/2:complete", bad, v, 422)
    ok = {"checks": {"standing": True, "jurisdiction": True,
                     "docs": {"date": True, "contact": True, "grounds": True, "signature": True}},
          "result": "ACCEPTED", "register_no": "FM-EFL-01/TEST"}
    d = _do(client, "lgl1", aid, "/steps/2:complete", ok, v)
    assert [s["state"] for s in d["steps"]][:4] == ["completed", "completed", "skipped", "current"]
    v = d["appeal"]["row_version"]
    # เวอร์ชันเก่า → 409
    _do(client, "lgl1", aid, "/steps/4:complete", {"route": "SUBCOMMITTEE"}, v - 1, 409)
    # หนังสือขอข้อเท็จจริงที่ยังไม่ตอบ → ปิดขั้น 4 ไม่ได้
    d = _do(client, "lgl1", aid, "/requests", {"target_type": "INTERNAL", "target_name": "สำนักกำกับกิจการไฟฟ้า",
                                                "subject": "ขอข้อเท็จจริง"}, v)
    v = d["appeal"]["row_version"]
    assert d["requests"][0]["due_date"] == (today + timedelta(days=7)).isoformat()
    _do(client, "lgl1", aid, "/steps/4:complete", {"route": "SUBCOMMITTEE"}, v, 422)
    d = _do(client, "lgl1", aid, f"/requests/{d['requests'][0]['request_id']}:respond", {}, v)
    v = d["appeal"]["row_version"]
    d = _do(client, "lgl1", aid, "/steps/4:complete", {"route": "SUBCOMMITTEE"}, v)
    v = d["appeal"]["row_version"]
    # ขั้น 5 ต้องมีวันประชุมครั้งแรกก่อน
    _do(client, "lgl1", aid, "/steps/5:complete", {}, v, 422)
    first = today - timedelta(days=20)
    d = _do(client, "lgl1", aid, "/milestones", {"code": "SUBC_FIRST_MEETING", "date": first.isoformat()}, v)
    v = d["appeal"]["row_version"]
    # ขยายเวลาเกิน 30 วัน → ปฏิเสธ; ภายใน 30 วัน → ผ่าน และวันครบกำหนดขั้น 5 เลื่อน
    _do(client, "lgl1", aid, "/milestones", {"code": "SUBC_EXT_NOTICE", "date": today.isoformat(),
                                             "extended_due": (first + timedelta(days=70)).isoformat()}, v, 422)
    d = _do(client, "lgl1", aid, "/milestones", {"code": "SUBC_EXT_NOTICE", "date": today.isoformat(),
                                                 "extended_due": (first + timedelta(days=50)).isoformat()}, v)
    assert d["steps"][4]["due_date"] == (first + timedelta(days=50)).isoformat()
    v = d["appeal"]["row_version"]
    for step, body in [(5, {}), (6, {}), (7, {"result": "ยกอุทธรณ์"})]:
        d = _do(client, "lgl1", aid, f"/steps/{step}:complete", body, v)
        v = d["appeal"]["row_version"]
    # หนังสือแจ้งผลต้องแจ้งสิทธิฟ้องศาลปกครอง
    _do(client, "lgl1", aid, "/steps/8:complete", {}, v, 400)
    d = _do(client, "lgl1", aid, "/steps/8:complete", {"rights_notice": True}, v)
    v = d["appeal"]["row_version"]
    # ปิดเรื่องได้เมื่อมีใบตอบรับ
    _do(client, "lgl1", aid, "/steps/9:complete", {}, v, 422)
    d = _do(client, "lgl1", aid, "/milestones", {"code": "NOTIFY_RECEIVED", "date": today.isoformat()}, v)
    d = _do(client, "lgl1", aid, "/steps/9:complete", {}, d["appeal"]["row_version"])
    assert d["appeal"]["status"] == "CLOSED" and d["appeal"]["decision_result"] == "ยกอุทธรณ์"
    assert next(c for c in d["clocks"] if c["key"] == "total")["state"] == "DONE_ON_TIME"


def test_late_appeal_must_be_rejected_with_reason(client):
    from datetime import date, timedelta
    today = date.today()
    aid = client.post("/api/v1/appeals", headers=h("lgl1"), json={
        "case_type": "APPEAL", "target_type": "ORDER", "filing_channel": "POST",
        "received_date": (today - timedelta(days=2)).isoformat(), "notice_date": (today - timedelta(days=45)).isoformat(),
        "subject": "อุทธรณ์ยื่นพ้นกำหนด", "party_code": "T-002", "energy_sector": "กิจการไฟฟ้า"}).json()["appeal_id"]
    d = client.get(f"/api/v1/appeals/{aid}", headers=h("dir1")).json()
    d = _do(client, "dir1", aid, ":assign", {"officer": "lgl2"}, d["appeal"]["row_version"])
    full = {"standing": True, "jurisdiction": True, "docs": {"date": True, "contact": True, "grounds": True, "signature": True}}
    v = d["appeal"]["row_version"]
    # ระบบคำนวณว่ายื่นพ้น 30 วัน → รับไม่ได้
    _do(client, "lgl2", aid, "/steps/2:complete", {"checks": full, "result": "ACCEPTED"}, v, 422)
    _do(client, "lgl2", aid, "/steps/2:complete", {"checks": full, "result": "REJECTED", "route": "DIRECT"}, v, 400)
    d = _do(client, "lgl2", aid, "/steps/2:complete", {"checks": full, "result": "REJECTED", "route": "DIRECT",
                                                       "note": "ยื่นพ้นกำหนด 30 วัน"}, v)
    assert [s["state"] for s in d["steps"]][:6] == ["completed", "completed", "skipped", "skipped", "skipped", "current"]
    assert d["appeal"]["admissibility"] == "REJECTED"


def test_revert_requires_reason(client):
    items = client.get("/api/v1/appeals", params={"status": "IN_PROGRESS"}, headers=h("dir1")).json()["items"]
    a = next(x for x in items if x["current_step"] and x["current_step"]["step_no"] >= 4)
    d = client.get(f"/api/v1/appeals/{a['appeal_id']}", headers=h("dir1")).json()
    _do(client, "dir1", a["appeal_id"], "/steps:revert", {}, d["appeal"]["row_version"], 400)
    d2 = _do(client, "dir1", a["appeal_id"], "/steps:revert", {"reason": "ทดสอบ"}, d["appeal"]["row_version"])
    cur = next(s["step_no"] for s in d2["steps"] if s["state"] == "current")
    assert cur < a["current_step"]["step_no"]


def test_staff_outside_legal_cannot_change_workflow(client):
    items = client.get("/api/v1/appeals", params={"status": "IN_PROGRESS"}, headers=h("stf1")).json()["items"]
    a = next(x for x in items if x["current_step"] and x["current_step"]["step_no"] == 4)
    d = client.get(f"/api/v1/appeals/{a['appeal_id']}", headers=h("stf1")).json()
    assert d["can_edit"] is False
    _do(client, "stf1", a["appeal_id"], "/steps/4:complete", {"route": "SUBCOMMITTEE"}, d["appeal"]["row_version"], 403)


def test_assistant_cites_every_sentence(client):
    r = client.post("/api/v1/assistant/answers", json={"question": "ยื่นอุทธรณ์ตามมาตรา 75 ภายในกี่วัน"},
                    headers=h("lgl1"))
    events = sse(r.text)
    sentences = [d for n, d in events if n == "sentence"]
    assert events[-1][1]["status"] == "ANSWERED" and sentences
    assert all(s["citations"] for s in sentences)


def test_assistant_no_evidence(client):
    r = client.post("/api/v1/assistant/answers",
                    json={"question": "หลักเกณฑ์การอุดหนุนภาษีคาร์บอนสำหรับโรงไฟฟ้านิวเคลียร์ขนาดเล็ก"}, headers=h("lgl1"))
    events = sse(r.text)
    assert events[-1][1]["status"] == "NO_EVIDENCE"
    assert not [d for n, d in events if n == "sentence"]


def test_assistant_never_leaks_restricted_sources(client):
    r = client.post("/api/v1/assistant/answers", json={"question": "ปรับทางปกครองท่อส่งก๊าซธรรมชาติ มาตรา 128"},
                    headers=h("stf1"))
    events = sse(r.text)
    srcs = next((d["sources"] for n, d in events if n == "sources"), [])
    assert all(s["classification"] != "restricted" for s in srcs)
    assert events[-1][1]["restricted_omitted"] >= 1


def test_audit_chain_valid(client):
    j = client.get("/api/v1/audit-events", headers=h("adm1")).json()
    assert j["chain_valid"] and j["items"]
    assert client.get("/api/v1/audit-events", headers=h("lgl1")).status_code == 403


def test_llm_output_is_validated_sentence_by_sentence(client, monkeypatch):
    """จำลอง LLM ที่ตอบปนประโยคไม่มีอ้างอิงและประโยคอ้างมาตราที่ไม่มีในแหล่ง → ต้องถูกตัดทิ้ง"""
    from app.assistant import rag
    from app.config import get_settings

    answer = ("ต้องยื่นอุทธรณ์ภายในสามสิบวันตามมาตรา 75 [S1]\n"
              "ประโยคที่ไม่มีการอ้างอิง\n"
              "ตามมาตรา 999 ต้องยื่นภายในเจ็ดวัน [S1]\n")
    monkeypatch.setattr(get_settings(), "llm_provider", "openai_compat")
    monkeypatch.setattr(rag, "stream_chat", lambda system, user: iter([answer[i:i + 5] for i in range(0, len(answer), 5)]))
    r = client.post("/api/v1/assistant/answers", json={"question": "ยื่นอุทธรณ์ตามมาตรา 75 ภายในกี่วัน"}, headers=h("lgl1"))
    events = sse(r.text)
    sentences = [d["text"] for n, d in events if n == "sentence"]
    assert sentences == ["ต้องยื่นอุทธรณ์ภายในสามสิบวันตามมาตรา 75"]
    assert events[-1][1]["dropped_sentences"] == 2
