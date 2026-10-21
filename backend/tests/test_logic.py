"""เทสต์ตรรกะล้วน (ไม่ต้องมีฐานข้อมูล)"""
from datetime import date

import pytest

from app.appeals import sla
from app.appeals import clocks
from app.appeals.workflow import Step, WorkflowError, complete, new_steps, revert
from app.assistant.validator import check_sentence, split_sentences
from app.auth import DEMO_USERS
from app.authz import can_edit_workflow, can_read_case, can_read_law_version
from app.law.resolver import NotYetInForce, Repealed, VersionWindow, pick_version
from app.search.thai import content_terms, normalize_ref_no, tokenize

# ---------------- point-in-time
SEC75 = [VersionWindow(1, date(2007, 12, 11), date(2021, 12, 31)), VersionWindow(2, date(2022, 1, 1), None)]


@pytest.mark.parametrize("as_of, expected", [
    (date(2019, 5, 15), 1), (date(2021, 12, 31), 1), (date(2022, 1, 1), 2), (date(2030, 1, 1), 2),
])
def test_pick_version(as_of, expected):
    assert pick_version(SEC75, as_of).version_no == expected


def test_not_yet_in_force():
    with pytest.raises(NotYetInForce):
        pick_version(SEC75, date(2000, 1, 1))


def test_repealed_has_no_fallback():
    repealed = [VersionWindow(1, date(2007, 1, 1), date(2015, 12, 31))]
    with pytest.raises(Repealed):
        pick_version(repealed, date(2020, 1, 1))


# ---------------- SLA
CAL = sla.CalendarDays()


@pytest.mark.parametrize("days_ago, state", [(10, "ON_TRACK"), (76, "NEAR_DUE"), (90, "NEAR_DUE"), (91, "OVERDUE")])
def test_sla_states(days_ago, state):
    today = date(2026, 10, 21)
    received = date.fromordinal(today.toordinal() - days_ago)
    assert sla.evaluate("IN_PROGRESS", received, 90, today, 14, CAL).state == state


def test_sla_missing_received_date_is_not_guessed():
    r = sla.evaluate("IN_PROGRESS", None, 90, date(2026, 10, 21), 14, CAL)
    assert r.state == "INCOMPLETE_DATA" and r.due_date is None


def test_sla_closed_reports_days_used():
    r = sla.evaluate("CLOSED", date(2024, 3, 28), 90, date(2026, 1, 1), 14, CAL, closed_date=date(2024, 6, 10))
    assert r.state == "CLOSED" and r.days_used == 74


def test_business_days_skip_weekend():
    bd = sla.BusinessDays()
    assert bd.add_days(date(2026, 10, 23), 1) == date(2026, 10, 26)  # ศุกร์ + 1 วันทำการ = จันทร์


# ---------------- workflow ตาม WI-EFL-04
def states(steps):
    return "".join({"pending": ".", "current": "C", "completed": "x", "skipped": "-"}[s.state]
                   for s in sorted(steps, key=lambda s: s.step_no))


def run(*actions):
    st = new_steps()
    for step, kw in actions:
        st = complete(st, step, **kw).steps
    return st


def test_new_appeal_starts_at_receive():
    assert states(new_steps()) == "C........"


def test_accepted_skips_correction():
    assert states(run((1, {}), (2, {"screening_result": "ACCEPTED"}))) == "xx-C....."


def test_need_correction_goes_to_step3():
    assert states(run((1, {}), (2, {"screening_result": "NEED_CORRECTION"}))) == "xxC......"


def test_rejected_goes_to_authority_without_fact_finding():
    st = run((1, {}), (2, {"screening_result": "REJECTED", "route": "SUBCOMMITTEE"}))
    assert states(st) == "xx--C...."
    st = run((1, {}), (2, {"screening_result": "REJECTED", "route": "DIRECT"}))
    assert states(st) == "xx---C..."


def test_direct_route_skips_subcommittee():
    st = run((1, {}), (2, {"screening_result": "ACCEPTED"}), (4, {"route": "DIRECT"}))
    assert states(st) == "xx-x-C..."


def test_full_path_closes_after_step9():
    st = run((1, {}), (2, {"screening_result": "ACCEPTED"}), (4, {"route": "SUBCOMMITTEE"}), (5, {}), (6, {}), (7, {}), (8, {}))
    tr = complete(st, 9)
    assert tr.closed and states(tr.steps) == "xx-xxxxxx"


def test_cannot_complete_non_current_step():
    with pytest.raises(WorkflowError):
        complete(new_steps(), 2)


def test_route_required_at_fact_finding():
    st = run((1, {}), (2, {"screening_result": "ACCEPTED"}))
    with pytest.raises(WorkflowError):
        complete(st, 4)


def test_revert_reopens_last_completed_and_clears_branch():
    st = run((1, {}), (2, {"screening_result": "ACCEPTED"}), (4, {"route": "DIRECT"}))
    new, back = revert(st)
    assert back == 4 and states(new) == "xx-C....."


# ---------------- กรอบเวลา
def test_filing_within_30_days():
    a = {"case_type": "APPEAL", "notice_date": date(2026, 1, 1), "received_date": date(2026, 1, 31), "sla_days": 90}
    f = clocks.legal_clocks(a, {}, date(2026, 2, 1), 14)[0]
    assert f["key"] == "filing" and f["state"] == "DONE_ON_TIME"
    a["received_date"] = date(2026, 2, 1)
    assert clocks.legal_clocks(a, {}, date(2026, 2, 1), 14)[0]["state"] == "DONE_LATE"


def test_total_90_days_stops_at_erc_resolution():
    a = {"case_type": "APPEAL", "notice_date": None, "received_date": date(2026, 1, 1), "sla_days": 90}
    total = next(c for c in clocks.legal_clocks(a, {"ERC_RESOLUTION": date(2026, 3, 20)}, date(2026, 6, 1), 14)
                 if c["key"] == "total")
    assert total["due"] == date(2026, 4, 1) and total["state"] == "DONE_ON_TIME"


def test_complaint_without_sla_is_not_applicable():
    a = {"case_type": "COMPLAINT", "received_date": date(2026, 1, 1), "sla_days": None}
    assert next(c for c in clocks.legal_clocks(a, {}, date(2026, 2, 1), 14) if c["key"] == "total")["state"] == "NOT_APPLICABLE"


def test_subcommittee_extension_moves_due_date():
    a = {"case_type": "APPEAL", "received_date": date(2026, 1, 1), "sla_days": 90, "route": "SUBCOMMITTEE"}
    ms = {"SUBC_FIRST_MEETING": date(2026, 1, 20), "SUBC_EXTENDED_DUE": date(2026, 3, 1)}
    sub = next(c for c in clocks.legal_clocks(a, ms, date(2026, 2, 25), 14) if c["key"] == "subcommittee")
    assert sub["due"] == date(2026, 3, 1) and sub["state"] == "NEAR_DUE"


def test_step_due_counts_business_days():
    st = [{"step_no": n, "state": "current" if n == 1 else "pending", "started_at": None, "completed_at": None,
           "note": None} for n in range(1, 10)]
    out = clocks.step_clocks(st, {"RECEIVED": date(2026, 10, 23)}, date(2026, 10, 23), sla.BusinessDays())
    assert out[0]["due_date"] == date(2026, 10, 26)  # รับวันศุกร์ ครบกำหนด 1 วันทำการ = วันจันทร์


# ---------------- authz
U = DEMO_USERS


def test_public_cannot_read_cases():
    assert not can_read_case(U["pub"], "internal", [])


def test_restricted_only_owner():
    assert can_read_case(U["lgl1"], "restricted", ["lgl1"])
    assert not can_read_case(U["lgl2"], "restricted", ["lgl1"])
    assert not can_read_case(U["stf1"], "restricted", ["lgl1"])
    assert can_read_case(U["stf1"], "internal", ["lgl1"])


def test_only_owning_lawyer_edits_workflow():
    assert can_edit_workflow(U["lgl1"], ["lgl1"])
    assert not can_edit_workflow(U["lgl2"], ["lgl1"])
    assert not can_edit_workflow(U["stf1"], ["stf1"])
    assert can_edit_workflow(U["dir1"], ["lgl1"])  # ผอ.คบ.


def test_draft_law_only_for_lawyers():
    assert can_read_law_version(U["pub"], "approved")
    assert not can_read_law_version(U["stf1"], "draft")
    assert can_read_law_version(U["lgl1"], "draft")


# ---------------- citation validator
SOURCES = {"S1": "มาตรา 75 ผู้รับใบอนุญาต ... ภายในสามสิบวัน", "S2": "อุทธรณ์ อธ. 14/2562 ไม่รับคำอุทธรณ์"}


def test_sentence_with_valid_citation_passes():
    assert check_sentence("ต้องยื่นอุทธรณ์ภายในสามสิบวันตามมาตรา 75 [S1]", SOURCES).ok


def test_sentence_without_citation_dropped():
    assert check_sentence("ต้องยื่นภายในสามสิบวัน", SOURCES).reason == "no_citation"


def test_unknown_source_dropped():
    assert check_sentence("ข้อความ [S9]", SOURCES).reason == "unknown_source"


def test_section_number_not_in_cited_source_dropped():
    assert not check_sentence("ตามมาตรา 128 ต้องปรับ [S1]", SOURCES).ok


def test_case_number_must_match_cited_source():
    assert check_sentence("เรื่อง อธ.14/2562 ไม่รับคำอุทธรณ์ [S2]", SOURCES).ok
    assert not check_sentence("เรื่อง อธ. 15/2562 ไม่รับคำอุทธรณ์ [S2]", SOURCES).ok


def test_split_sentences_keeps_partial_tail():
    done, tail = split_sentences("บรรทัดหนึ่ง [S1]\nบรรทัดสอ")
    assert done == ["บรรทัดหนึ่ง [S1]"] and tail == "บรรทัดสอ"


# ---------------- Thai tokenizer
def test_thai_marks_preserved():
    assert "อุทธรณ์" in tokenize("การอุทธรณ์คำสั่ง")


def test_question_words_ignored():
    assert "เปรียบเทียบ" not in content_terms("เปรียบเทียบมาตรา 75")


def test_ref_no_normalized():
    assert normalize_ref_no("อธ. 104/2567") == normalize_ref_no("อธ104 / 2567") == "อธ104/2567"
