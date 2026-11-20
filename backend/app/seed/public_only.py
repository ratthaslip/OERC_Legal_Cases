"""เครื่องที่ตั้ง PUBLIC_DATA_ONLY=true (เช่น เครื่องพัฒนาบนคลาวด์ส่วนตัว): ล้างข้อมูลภายในของสำนักงานออก

  python -m app.seed.public_only export-register          # ทะเบียนเฉพาะคอลัมน์สาธารณะ → /data/raw/register-public.json
  python -m app.seed.public_only purge                    # แสดงสิ่งที่จะลบ (ยังไม่ลบ)
  python -m app.seed.public_only purge --yes              # ลบไฟล์ต้นฉบับภายใน ผล OCR ของเอกสารภายใน และร่างภายใน

ลำดับ: ตั้ง PUBLIC_DATA_ONLY=true → seed (ยังมี register.xlsx) → export-register → purge --yes → ลบ register.xlsx บนเครื่อง → seed
"""
import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import text

from ..config import public_data_only
from ..db import get_engine, rows
from ..storage import RawStore
from .import_register import export_register_json

HERE = Path(__file__).parent
DEFAULT_JSON = "/data/raw/register-public.json"


def internal_shas(conn) -> set[str]:
    listed = {ln.strip() for ln in (HERE / "internal_source_sha256.txt").read_text(encoding="utf-8").splitlines()
              if ln.strip() and not ln.startswith("#")}
    db = {r["sha256"] for r in rows(conn, """
        SELECT f.sha256 FROM raw_file f WHERE f.classification <> 'public' OR EXISTS (
          SELECT 1 FROM raw_file_link l JOIN legal_instrument i ON i.eli_id = l.instrument_eli
          WHERE l.sha256 = f.sha256 AND i.classification <> 'public')""")}
    return listed | db


def purge(apply: bool) -> None:
    store = RawStore()
    eng = get_engine()
    with eng.begin() as conn:
        shas = internal_shas(conn)
        files = rows(conn, "SELECT sha256, stored_path, original_name FROM raw_file WHERE sha256 = ANY(:s)", s=list(shas))
        ups = rows(conn, """SELECT upload_id, original_name FROM law_upload
                            WHERE classification = 'internal' OR draft->>'classification' = 'internal'""")
        work = Path(os.environ.get("LEGAL_IMPORT_WORK", "/data/raw/ocr-work"))
        ocr = [p for p in work.rglob("*") if p.is_file() and p.name[:64] in shas] if work.is_dir() else []
        print(f"ไฟล์ต้นฉบับภายในในคลัง: {len(files)}")
        for f in files[:10]:
            print("  -", f["original_name"])
        print(f"ผล OCR ของเอกสารภายใน ({work}): {len(ocr)} ไฟล์")
        print(f"ร่างเอกสารภายในจากหน้าเว็บ: {len(ups)}")
        if not apply:
            print("\nยังไม่ได้ลบ — รันซ้ำด้วย --yes เพื่อลบ")
            return
        for f in files:
            p = store.path(f["stored_path"])
            if p.exists():
                p.unlink()
        conn.execute(text("DELETE FROM raw_file_link WHERE sha256 = ANY(:s)"), {"s": list(shas)})
        conn.execute(text("DELETE FROM raw_file WHERE sha256 = ANY(:s)"), {"s": list(shas)})
        conn.execute(text("""DELETE FROM law_upload WHERE classification = 'internal'
                             OR draft->>'classification' = 'internal'"""))
        for p in ocr:
            p.unlink()
    print("ลบแล้ว — ต่อไป: ลบไฟล์ทะเบียนต้นฉบับและโฟลเดอร์ข้อมูลภายในบนเครื่อง (ดู README) แล้วรัน seed อีกครั้ง")


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export-register")
    e.add_argument("--out", default=os.environ.get("REGISTER_PUBLIC_JSON", DEFAULT_JSON))
    pg = sub.add_parser("purge")
    pg.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    if not public_data_only():
        sys.exit("ตั้ง PUBLIC_DATA_ONLY=true ใน .env และ restart api ก่อน")
    if a.cmd == "export-register":
        with get_engine().begin() as conn:
            n = export_register_json(conn, Path(a.out))
        print(f"เขียนทะเบียน {n} แถว (เฉพาะคอลัมน์สาธารณะ) → {a.out}")
    else:
        purge(a.yes)


if __name__ == "__main__":
    main(sys.argv[1:])
