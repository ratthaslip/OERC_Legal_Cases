"""ระดับ 1–2: คำถามเชิงโครงสร้างกับตัวบทจริง (ประวัติการแก้ไข, บทนิยาม) และการขยายตามเส้นความสัมพันธ์"""
import json
import os
from datetime import date

import pytest

from app.assistant.structured import _phrase_score, th_date


def test_th_date():
    assert th_date(date(2017, 2, 12)) == "12 ก.พ. 2560"


def test_phrase_score_separates_similar_titles():
    q = "ตามระเบียบว่าด้วยการยื่นข้อพิพาทระหว่างผู้รับใบอนุญาต"
    dispute = "ระเบียบ ว่าด้วยหลักเกณฑ์ วิธีการ เงื่อนไขการยื่นข้อพิพาทและการพิจารณาวินิจฉัยข้อพิพาทระหว่างผู้รับใบอนุญาต"
    complaint = "ระเบียบ ว่าด้วยหลักเกณฑ์การยื่นเรื่องร้องเรียน และข้อพิพาทระหว่างผู้ใช้พลังงานกับผู้รับใบอนุญาต"
    assert _phrase_score(q, dispute) > _phrase_score(q, complaint)


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


def ask(c, q, user="pub", as_of=None):
    body = {"question": q, **({"as_of": as_of} if as_of else {})}
    ev = []
    for b in c.post("/api/v1/assistant/answers", json=body, headers={"X-Demo-User": user}).text.strip().split("\n\n"):
        if b.startswith("event:"):
            n, d = b.split("\n", 1)
            ev.append((n[7:].strip(), json.loads(d[6:])))
    return (next(d for n, d in ev if n == "done"), [d for n, d in ev if n == "sentence"],
            next((d["sources"] for n, d in ev if n == "sources"), []))


def test_amendment_history(client):
    done, sents, srcs = ask(client, "ข้อ 21 ของระเบียบ กกพ. ว่าด้วยหลักเกณฑ์การยื่นเรื่องร้องเรียน ถูกแก้ไขโดยฉบับใด")
    assert done["route"] == "amendments" and "มี 2 รุ่น" in sents[0]["text"]
    assert "2558" in sents[2]["text"] and all(s["eli_id"].endswith("complaint/clause-21") for s in srcs)


def test_amendment_ambiguous_falls_back(client):
    done, _, _ = ask(client, "ข้อ 5 ถูกแก้ไขหรือไม่")  # มีข้อ 5 หลายฉบับและไม่ระบุฉบับ → ไม่เดา
    assert done.get("route") != "amendments"


def test_definition_picks_named_instrument(client):
    done, sents, srcs = ask(client, "คู่กรณี ตามพระราชบัญญัติวิธีปฏิบัติราชการทางปกครอง หมายถึงใคร")
    assert done["route"] == "definition" and [s["eli_id"] for s in srcs] == ["eli/th/act/2539/admin-procedure/sec-5"]
    assert "“คู่กรณี” หมายความว่า" in sents[0]["text"]


def test_definition_respects_as_of(client):
    # ข้อ 3/1 (สถานีอัดประจุไฟฟ้า) มีผล 21 ต.ค. 2560 — ถามย้อนไปก่อนหน้านั้นต้องไม่อ้าง
    _, _, srcs = ask(client, "สถานีอัดประจุไฟฟ้าตามระเบียบการขอรับใบอนุญาตหมายถึงอะไร", as_of="2016-06-01")
    assert all(not (s.get("eli_id") or "").endswith("clause-3-1") for s in srcs)
    done, _, srcs = ask(client, "สถานีอัดประจุไฟฟ้าตามระเบียบการขอรับใบอนุญาตหมายถึงอะไร")
    assert done["route"] == "definition" and srcs[0]["eli_id"].endswith("licensing/clause-3-1")


def test_graph_expansion_adds_parent_section(client):
    """ค้นได้ข้อของระเบียบ → ดึงมาตราแม่บทใน พ.ร.บ. มาด้วย (ทะเบียนว่างในเทสต์นี้ จึงต้องไม่มีอะไรเพิ่มและไม่ล่ม)"""
    done, sents, srcs = ask(client, "การยื่นอุทธรณ์ด้วยวาจาต่อพนักงานของสำนักงาน")
    assert done["status"] == "ANSWERED" and all(s.get("via") is None for s in srcs)


def test_section_search_prefers_energy_act(client):
    """ค้น "มาตรา 11" → ทุกฉบับที่มีมาตรา 11 โดย พ.ร.บ.พลังงานมาก่อน เว้นแต่ระบุชื่อฉบับอื่นในคำค้น"""
    items = client.get("/api/v1/laws/search", params={"q": "มาตรา 11"}, headers={"X-Demo-User": "pub"}).json()["items"]
    exact = [h["eli_id"] for h in items if h.get("exact_ref")]
    assert exact[0] == "eli/th/act/2550/energy-industry/sec-11"
    assert "eli/th/act/2539/admin-procedure/sec-11" in exact
    named = client.get("/api/v1/laws/search", params={"q": "มาตรา 11 วิธีปฏิบัติราชการทางปกครอง"},
                       headers={"X-Demo-User": "pub"}).json()["items"]
    assert named[0]["eli_id"] == "eli/th/act/2539/admin-procedure/sec-11"
    order = [i["eli_id"] for i in client.get("/api/v1/laws/instruments").json()["items"]]
    assert order[0] == "eli/th/act/2550/energy-industry"
    assert order.index("eli/th/act/2539/admin-procedure") > order.index("eli/th/reg/erc/2552/appeal")


def test_demo_cases_flagged_and_excluded_from_assistant(client, monkeypatch):
    """เรื่อง/คดีที่ seed สร้างติดธง is_demo และผู้ช่วย AI ไม่ใช้เป็นหลักฐาน เว้นแต่เปิด RAG_INCLUDE_DEMO_CASES"""
    from app.config import get_settings
    items = client.get("/api/v1/appeals", headers={"X-Demo-User": "dir1"}).json()["items"]
    assert items and all(a["is_demo"] for a in items)
    cases = client.get("/api/v1/court-cases", headers={"X-Demo-User": "dir1"}).json()["items"]
    assert cases and all(c["is_demo"] for c in cases)
    q = "อุทธรณ์คำสั่งพักใช้ใบอนุญาตขนส่งก๊าซธรรมชาติทางท่อ"
    done, _, srcs = ask(client, q, user="dir1")
    assert done["demo_excluded"] is True and all(s["kind"] in ("provision", "register") for s in srcs)
    monkeypatch.setattr(get_settings(), "rag_include_demo_cases", True)
    done, _, srcs = ask(client, q, user="dir1")
    assert not done.get("demo_excluded") and any(s["kind"] == "appeal" and s["is_demo"] for s in srcs)
