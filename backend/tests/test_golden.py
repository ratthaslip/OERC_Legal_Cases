"""ชุดคำถามทดสอบผู้ช่วย AI: ทุกข้อต้องอ้างตัวบทที่มีอยู่จริง และหลักฐานต้องตรงกับตัวบท"""
import json
from pathlib import Path

from eval.build_golden import build, check

GOLDEN = Path(__file__).parents[1] / "eval" / "golden_set.json"


def test_golden_set_unique_items():
    items = build()
    assert len(items) == 78
    assert len({i["id"] for i in items}) == len(items)
    assert len({i["question"] for i in items}) == len(items)


def test_golden_evidence_matches_law_text():
    assert check(build()) == []


def test_golden_json_is_up_to_date():
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == build()


def test_answered_items_have_sources():
    for i in build():
        if i["expected_status"] == "ANSWERED":
            assert i["expected_sources"] or i["expected_title_contains"] or i["must_include"], i["id"]
