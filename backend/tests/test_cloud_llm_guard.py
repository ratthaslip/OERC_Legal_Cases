"""LLM ภายนอก (คลาวด์): ส่งให้ LLM เฉพาะตัวบทสาธารณะ — ทะเบียน (ไฟล์ภายใน) ตราสารภายใน และสำนวน ต้องไม่ออกนอกระบบ"""
import json
from pathlib import Path

import pytest

from app.config import llm_is_external

LAWS = Path(__file__).resolve().parents[1] / "app" / "seed" / "laws"


@pytest.mark.parametrize("provider,url,override,expected", [
    ("openai_compat", "https://api.openai.com/v1", "auto", True),
    ("openai_compat", "https://my-resource.openai.azure.com/openai/v1", "auto", True),
    ("anthropic", "https://api.anthropic.com", "auto", True),
    ("openai_compat", "http://host.docker.internal:11434/v1", "auto", False),
    ("openai_compat", "http://vllm:8000/v1", "auto", False),
    ("openai_compat", "http://10.20.1.5:8000/v1", "auto", False),
    ("openai_compat", "http://192.168.1.10/v1", "auto", False),
    ("openai_compat", "https://llm.erc.local/v1", "auto", False),
    ("extractive", "", "auto", False),
    ("openai_compat", "http://10.20.1.5:8000/v1", "true", True),      # บังคับ: เช่น vLLM ของผู้ให้บริการภายนอกใน VPN
    ("openai_compat", "https://api.openai.com/v1", "false", False),   # บังคับปิด (ผู้ดูแลยืนยันเองว่าทำได้)
])
def test_llm_is_external(provider, url, override, expected):
    assert llm_is_external(provider, url, override) is expected


@pytest.fixture(scope="module")
def client(seeded_client):
    return seeded_client


def _internal_title() -> tuple[str, str]:
    for f in sorted(LAWS.rglob("*.json")):
        for inst in json.loads(f.read_text(encoding="utf-8")):
            if inst.get("classification") == "internal":
                return inst["id"], inst["title"]
    pytest.skip("ไม่มีตราสารภายใน")


def _ask(client, monkeypatch, q: str, external: bool, user: str = "lgl1"):
    from app.admin import llm_config
    from app.assistant import rag
    cfg = llm_config.LlmConfig("openai_compat", "https://api.example.com/v1", "m", "", 30.0, external, {})
    monkeypatch.setattr(llm_config, "effective", lambda: cfg)
    sent: list[str] = []

    def fake_llm(system, prompt, cfg=None):
        sent.append(prompt)
        yield "ไม่มีข้อความจากโมเดล\n"  # ไม่มีอ้างอิง → ถูกตัดทิ้ง; สนใจแค่ข้อความที่ส่งออก
    monkeypatch.setattr(rag, "stream_chat", fake_llm)
    ev = {}
    for b in client.post("/api/v1/assistant/answers", json={"question": q},
                         headers={"X-Demo-User": user}).text.strip().split("\n\n"):
        if b.startswith("event:"):
            name, data = b.split("\n", 1)
            ev.setdefault(name[7:].strip(), []).append(json.loads(data[6:]))
    return ev, "\n".join(sent)


def test_external_llm_never_receives_internal_instrument(client, monkeypatch):
    eli, title = _internal_title()
    ev, sent = _ask(client, monkeypatch, title[:80], external=True)
    srcs = ev["sources"][0]["sources"]
    assert any((x.get("eli_id") or "").startswith(eli + "/") for x in srcs)  # เจ้าหน้าที่ยังเห็นแหล่งนี้
    assert title[:40] not in sent                                             # แต่ไม่ถูกส่งให้ LLM
    done = ev["done"][0]
    assert done["withheld_from_llm"] >= 1
    assert any(s["citations"] for s in ev.get("sentence", []))                # ตอบด้วยการยกข้อความแทน


def test_external_llm_never_receives_register_rows(client, monkeypatch):
    ev, sent = _ask(client, monkeypatch, "ระเบียบ กกพ. ว่าด้วยเรื่องทดสอบ ใช้บังคับเมื่อใด", external=True)
    kinds = {x["kind"] for x in ev["sources"][0]["sources"]}
    assert "ทะเบียนกฎหมาย" not in sent
    if "register" in kinds:
        assert ev["done"][0]["withheld_from_llm"] >= 1


def test_internal_llm_receives_everything(client, monkeypatch):
    _eli, title = _internal_title()
    ev, sent = _ask(client, monkeypatch, title[:80], external=False)
    assert title[:40] in sent and ev["done"][0]["withheld_from_llm"] == 0


def test_public_law_still_goes_to_external_llm(client, monkeypatch):
    _ev, sent = _ask(client, monkeypatch, "การอุทธรณ์คำสั่ง กกพ. ต้องยื่นภายในกี่วัน", external=True, user="pub")
    assert "มาตรา 121" in sent or "ข้อ 9" in sent
