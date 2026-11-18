"""จัดการไฟล์ต้นฉบับ (PDF ตัวบท, Excel ทะเบียน) ในที่เก็บไฟล์ — ใช้บนเซิร์ฟเวอร์

  python -m app.seed.raw_files add <ไฟล์> --class public --source "ราชกิจจานุเบกษา" [--from "ฝ่ายกฎหมาย"] [--received 2026-11-17] [--note ...]
  python -m app.seed.raw_files manifest <manifest.csv> --dir <โฟลเดอร์ไฟล์>     # นำเข้าเป็นชุดตาม manifest
  python -m app.seed.raw_files zip <ไฟล์.zip> --class public --from "ฝ่ายกฎหมาย"  # zip จาก Google Drive: ไม่ต้องแตกไฟล์
  python -m app.seed.raw_files verify                                          # ตรวจว่าไฟล์ครบและ hash ตรงทุกไฟล์
  python -m app.seed.raw_files list

manifest.csv (UTF-8): file,classification,source,received_from,received_at,note
ผู้เพิ่มไฟล์ใช้ค่า IMPORT_ACTOR (ไม่ตั้ง = "cli")
"""
import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_engine, rows
from ..storage import RawStore, guess_mime


def pdf_pages(path: Path) -> int | None:
    if path.suffix.lower() != ".pdf":
        return None
    try:
        import pymupdf
        with pymupdf.open(path) as d:
            return d.page_count
    except Exception:  # noqa: BLE001 — ไม่มี pymupdf หรือไฟล์เสีย: ไม่บังคับ
        return None


def add_file(conn: Connection, src: Path, classification: str, source: str | None = None,
             received_from: str | None = None, received_at: date | None = None, note: str | None = None,
             original_name: str | None = None, store: RawStore | None = None) -> dict:
    store = store or RawStore()
    st = store.put_file(src, classification)
    name = original_name or src.name
    conn.execute(text("""
        INSERT INTO raw_file (sha256, classification, original_name, mime, size_bytes, pages, stored_path, source,
                              received_from, received_at, note, added_by)
        VALUES (:sha, :cls, :name, :mime, :size, :pages, :rel, :src, :frm, :rcv, :note, :by)
        ON CONFLICT (sha256) DO NOTHING"""), {
        "sha": st.sha256, "cls": classification, "name": name, "mime": guess_mime(name), "size": st.size,
        "pages": pdf_pages(src), "rel": st.rel_path, "src": source, "frm": received_from, "rcv": received_at,
        "note": note, "by": os.environ.get("IMPORT_ACTOR") or "cli"})
    return {"sha256": st.sha256, "stored": st.rel_path, "new_file": st.created, "name": name}


def add_zip(conn: Connection, zip_path: Path, classification: str, source: str | None = None,
            received_from: str | None = None, received_at: date | None = None) -> list[dict]:
    """เก็บทุกไฟล์ใน zip เข้าที่เก็บไฟล์ — ชื่อไทยใน zip จาก Google Drive ไม่ติดธง UTF-8 จึงถอดเอง
    และไม่แตกลงดิสก์ด้วยชื่อเดิม (ชื่อไทยยาวเกินขีดจำกัดของระบบไฟล์ได้) — ชื่อเดิมพร้อมโฟลเดอร์เก็บใน note"""
    import tempfile
    import zipfile
    out = []
    with zipfile.ZipFile(zip_path) as z, tempfile.TemporaryDirectory() as tmp:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("utf-8")
                except UnicodeError:
                    pass
            ext = Path(name).suffix.lower()
            if ext not in (".pdf", ".doc", ".docx", ".xlsx", ".xls"):
                continue
            tmp_file = Path(tmp) / f"member{ext}"
            tmp_file.write_bytes(z.read(info))
            res = add_file(conn, tmp_file, classification, source, received_from, received_at,
                           note=f"{zip_path.name}: {name}", original_name=Path(name).name)
            out.append(res)
    return out


def verify_all(conn: Connection, store: RawStore | None = None) -> list[dict]:
    store = store or RawStore()
    out = []
    for r in rows(conn, "SELECT sha256, stored_path, original_name FROM raw_file ORDER BY added_at"):
        out.append({**r, "status": store.verify(r["stored_path"], r["sha256"])})
    return out


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.seed.raw_files")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("file")
    a.add_argument("--class", dest="cls", choices=["public", "internal"], required=True)
    a.add_argument("--source")
    a.add_argument("--from", dest="frm")
    a.add_argument("--received", type=date.fromisoformat)
    a.add_argument("--note")
    a.add_argument("--name", help="ชื่อไฟล์เดิม ถ้าต่างจากชื่อไฟล์ที่วาง")
    m = sub.add_parser("manifest")
    m.add_argument("csv")
    m.add_argument("--dir", required=True)
    zp = sub.add_parser("zip")
    zp.add_argument("zip", nargs="+")
    zp.add_argument("--class", dest="cls", choices=["public", "internal"], required=True)
    zp.add_argument("--source")
    zp.add_argument("--from", dest="frm")
    zp.add_argument("--received", type=date.fromisoformat)
    sub.add_parser("verify")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    with get_engine().begin() as conn:
        if args.cmd == "add":
            print(add_file(conn, Path(args.file), args.cls, args.source, args.frm, args.received, args.note, args.name))
        elif args.cmd == "manifest":
            with open(args.csv, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    res = add_file(conn, Path(args.dir) / r["file"], r["classification"], r.get("source") or None,
                                   r.get("received_from") or None,
                                   date.fromisoformat(r["received_at"]) if r.get("received_at") else None,
                                   r.get("note") or None, r.get("original_name") or None)
                    print(("+ " if res["new_file"] else "= ") + f"{res['sha256'][:12]} {res['name']}")
        elif args.cmd == "zip":
            for zf in args.zip:
                res = add_zip(conn, Path(zf), args.cls, args.source, args.frm, args.received)
                new = sum(r["new_file"] for r in res)
                print(f"{Path(zf).name}: {len(res)} ไฟล์ (ใหม่ {new}, มีอยู่แล้ว {len(res) - new})")
        elif args.cmd == "verify":
            bad = 0
            for r in verify_all(conn):
                bad += r["status"] != "ok"
                print(f"{r['status']:<8} {r['sha256'][:12]} {r['original_name']}")
            sys.exit(1 if bad else 0)
        else:
            for r in rows(conn, """SELECT left(sha256, 12) AS sha, classification, size_bytes, original_name,
                                          (SELECT string_agg(instrument_eli, ', ') FROM raw_file_link l
                                            WHERE l.sha256 = f.sha256) AS laws FROM raw_file f ORDER BY added_at"""):
                print(f"{r['sha']} {r['classification']:<8} {r['size_bytes']:>10} {r['original_name']}  → {r['laws'] or '-'}")


if __name__ == "__main__":
    main(sys.argv[1:])
