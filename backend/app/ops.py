"""คำสั่งสำหรับผู้ดูแลระบบ

  python -m app.ops check                         สรุปจำนวนข้อมูล + ตรวจ hash chain ของ audit log (ใช้หลังกู้คืน)
  python -m app.ops check --json                  ผลเป็น JSON (เทียบก่อน/หลังสำรอง-กู้คืนอัตโนมัติ)
  python -m app.ops audit-verify --file PATH      เทียบสำเนา audit log นอกฐาน (AUDIT_SINK=file:…) กับฐานข้อมูล
exit code 0 = ปกติ, 1 = พบปัญหา
"""
import argparse
import json
import sys

from sqlalchemy import text

from .audit import verify_chain
from .db import get_engine

TABLES = ("legal_instrument", "provision_version", "law_register", "raw_file", "law_upload", "appeal", "court_case",
          "citation_link", "audit_event", "app_setting", "embedding_index")


def check(audit_seq: int | None = None) -> dict:
    with get_engine().connect() as c:
        counts = {}
        for t in TABLES:
            if c.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar():
                counts[t] = c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        ok, broken = verify_chain(c)
        last = c.execute(text("SELECT seq, hash FROM audit_event ORDER BY seq DESC LIMIT 1")).mappings().first()
        at = c.execute(text("SELECT hash FROM audit_event WHERE seq = :s"), {"s": audit_seq}).scalar() \
            if audit_seq is not None else None
        version = c.execute(text("SELECT version_num FROM alembic_version")).scalar() \
            if c.execute(text("SELECT to_regclass('alembic_version')")).scalar() else None
    return {"counts": counts, "audit_chain_valid": ok, "audit_broken_at": broken,
            "audit_last_seq": last["seq"] if last else None, "audit_last_hash": last["hash"] if last else None,
            "schema_version": version, **({"audit_hash_at_seq": at} if audit_seq is not None else {})}


def audit_verify(path: str) -> dict:
    """ทุกบรรทัดในไฟล์ต้องตรงกับแถวในฐาน (seq + hash) และทุกแถวในฐานต้องมีในไฟล์"""
    file_ev: dict[int, str] = {}
    bad_lines = 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            try:
                ev = json.loads(ln)
                file_ev[int(ev["seq"])] = ev["hash"]
            except (ValueError, KeyError):
                bad_lines += 1
    with get_engine().connect() as c:
        db_ev = {r[0]: r[1] for r in c.execute(text("SELECT seq, hash FROM audit_event"))}
        chain_ok, broken = verify_chain(c)
    mismatched = sorted(s for s in file_ev.keys() & db_ev.keys() if file_ev[s] != db_ev[s])
    only_file = sorted(file_ev.keys() - db_ev.keys())     # มีในสำเนาแต่หายจากฐาน = ถูกลบ
    only_db = sorted(db_ev.keys() - file_ev.keys())       # ยังไม่ถูกส่งออก (หรือส่งไม่สำเร็จ)
    return {"file_events": len(file_ev), "db_events": len(db_ev), "db_chain_valid": chain_ok, "db_broken_at": broken,
            "mismatched_seq": mismatched[:50], "missing_from_db": only_file[:50], "missing_from_file": only_db[:50],
            "unreadable_lines": bad_lines,
            "ok": chain_ok and not mismatched and not only_file and not bad_lines}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    c.add_argument("--audit-seq", type=int, help="คืน hash ของ audit event ลำดับนี้ด้วย (ตรวจหลังกู้คืน)")
    v = sub.add_parser("audit-verify")
    v.add_argument("--file", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "check":
        r = check(a.audit_seq)
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
        else:
            for k, n in r["counts"].items():
                print(f"{k:<20} {n:>8}")
            print(f"schema_version       {r['schema_version']}")
            print(f"audit chain          {'OK' if r['audit_chain_valid'] else 'BROKEN at seq ' + str(r['audit_broken_at'])}")
        return 0 if r["audit_chain_valid"] else 1
    r = audit_verify(a.file)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
