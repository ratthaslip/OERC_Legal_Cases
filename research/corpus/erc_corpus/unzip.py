"""Step 0: unpack the legal team's Drive zips. Thai names inside the zips are often
stored in cp437; they are decoded, and every file is written under a short hashed
name (<sha1[:12]><ext>) with manifest.csv mapping it back to zip + original path.

    python -m erc_corpus.unzip raw drive-download-*.zip
"""
import csv, hashlib, os, pathlib, sys, zipfile


def main(out, zips):
    out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not (info.flag_bits & 0x800):  # not flagged UTF-8
                    try:
                        name = name.encode("cp437").decode("utf-8")
                    except UnicodeError:
                        pass
                ext = pathlib.Path(name).suffix.lower()
                h = hashlib.sha1((os.path.basename(zp) + "/" + name).encode()).hexdigest()[:12]
                with z.open(info) as src, open(out / f"{h}{ext}", "wb") as f:
                    f.write(src.read())
                rows.append({"file_id": h, "ext": ext, "zip": os.path.basename(zp),
                             "path": name, "size": info.file_size})
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file_id", "ext", "zip", "path", "size"])
        w.writeheader(); w.writerows(rows)
    print(f"files={len(rows)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
