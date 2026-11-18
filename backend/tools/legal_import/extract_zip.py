"""แตก zip จาก Google Drive: ถอดชื่อไทยให้ถูก, เก็บไฟล์ด้วยชื่อสั้น (sha256) + manifest.json ชื่อเดิม"""
import hashlib, json, sys, zipfile
from pathlib import Path
src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
(out / "files").mkdir(exist_ok=True)
man = []
with zipfile.ZipFile(src) as z:
    for i in z.infolist():
        if i.is_dir():
            continue
        name = i.filename
        if not i.flag_bits & 0x800:
            try:
                name = i.filename.encode("cp437").decode("utf-8")
            except UnicodeError:
                pass
        data = z.read(i)
        sha = hashlib.sha256(data).hexdigest()
        ext = Path(name).suffix.lower()
        (out / "files" / f"{sha}{ext}").write_bytes(data)
        man.append({"path": name, "folder": str(Path(name).parent), "name": Path(name).name, "ext": ext,
                    "size": len(data), "sha256": sha})
(out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(man), "files")
