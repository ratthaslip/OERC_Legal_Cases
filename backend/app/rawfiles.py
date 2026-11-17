"""เปิดไฟล์ต้นฉบับ (PDF ตัวบท ฯลฯ) จากที่เก็บไฟล์ — ตรวจชั้นความลับและบันทึก audit ทุกครั้ง"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse
from sqlalchemy.engine import Connection

from . import audit, authz
from .auth import User, current_user
from .db import get_conn, one, rows
from .storage import RawStore

router = APIRouter(prefix="/api/v1/raw", tags=["raw"])


def sources_of(conn: Connection, instrument_eli: str, user: User) -> list[dict]:
    """ไฟล์ต้นฉบับของตัวบทฉบับหนึ่ง (ไฟล์ภายในเห็นเฉพาะเจ้าหน้าที่)"""
    out = rows(conn, """SELECT l.sha256, l.role, l.pages, f.original_name AS name, f.classification, f.pages AS page_count,
                               f.size_bytes, f.source, (f.sha256 IS NOT NULL) AS available
                        FROM raw_file_link l LEFT JOIN raw_file f USING (sha256)
                        WHERE l.instrument_eli = :e ORDER BY l.role DESC, l.sha256""", e=instrument_eli)
    internal = user.role in authz.INTERNAL_ROLES
    return [r for r in out if internal or r["classification"] != "internal"]


@router.get("/{sha256}")
def open_raw(sha256: str = Path(pattern=r"^[0-9a-fA-F]{64}$"), download: bool = False, user: User = Depends(current_user),
             conn: Connection = Depends(get_conn)):
    f = one(conn, "SELECT * FROM raw_file WHERE sha256 = :s", s=sha256.lower())
    if not f or (f["classification"] == "internal" and user.role not in authz.INTERNAL_ROLES):
        raise authz.not_found()
    path = RawStore().path(f["stored_path"])
    if not path.exists():
        raise authz.not_found()
    audit.record(conn, user, "raw.view", f"raw/{f['sha256']}", detail={"name": f["original_name"]})
    disp = "attachment" if download else "inline"
    return FileResponse(path, media_type=f["mime"],
                        headers={"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(f['original_name'])}"})
