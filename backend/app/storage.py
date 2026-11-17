"""ที่เก็บไฟล์ต้นฉบับ (raw files) แบบโฟลเดอร์ — โครงสร้างเดียวกับ object storage เพื่อย้ายไป S3 ภายหลังได้

  {RAW_STORE_DIR}/{public|internal}/{sha256[:2]}/{sha256}{.ext}

- ระบุไฟล์ด้วย SHA-256 ของเนื้อไฟล์: ไฟล์เดียวกันส่งมาซ้ำก็เก็บครั้งเดียว และตรวจได้ว่าไฟล์ไม่ถูกแก้
- เขียนครั้งเดียว (write-once): ไม่เขียนทับไฟล์ที่มีอยู่ และตั้งสิทธิ์อ่านอย่างเดียว (0444)
- public = ตัวบทกฎหมาย (เผยแพร่ได้) · internal = ทะเบียน/คู่มือภายใน (เจ้าหน้าที่เท่านั้น ห้ามส่งให้ cloud LLM)
- ย้ายไป object storage: แทนคลาสนี้ด้วยตัวที่คุยผ่าน S3 API โดยใช้ key เดียวกัน (bucket = classification)
"""
import hashlib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings

CLASSES = ("public", "internal")


@dataclass
class Stored:
    sha256: str
    size: int
    rel_path: str
    created: bool


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class RawStore:
    def __init__(self, base: str | Path | None = None):
        self.base = Path(base or get_settings().raw_store_dir)

    @staticmethod
    def rel_path(sha: str, classification: str, ext: str) -> str:
        if classification not in CLASSES:
            raise ValueError(f"classification ต้องเป็น {CLASSES}")
        return f"{classification}/{sha[:2]}/{sha}{ext.lower()}"

    def path(self, rel: str) -> Path:
        p = (self.base / rel).resolve()
        if self.base.resolve() not in p.parents:
            raise ValueError("path นอกที่เก็บไฟล์")
        return p

    def put_file(self, src: Path, classification: str) -> Stored:
        sha = sha256_file(src)
        rel = self.rel_path(sha, classification, src.suffix)
        dst = self.path(rel)
        if dst.exists():
            return Stored(sha, dst.stat().st_size, rel, False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".part")
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            for chunk in iter(lambda: fi.read(1 << 20), b""):
                fo.write(chunk)
        if sha256_file(tmp) != sha:  # คัดลอกไม่สมบูรณ์
            tmp.unlink()
            raise IOError(f"hash ไม่ตรงหลังคัดลอก {src}")
        os.replace(tmp, dst)
        os.chmod(dst, 0o444)
        return Stored(sha, dst.stat().st_size, rel, True)

    def verify(self, rel: str, sha: str) -> str:
        p = self.path(rel)
        if not p.exists():
            return "missing"
        return "ok" if sha256_file(p) == sha else "mismatch"


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
