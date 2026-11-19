import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Connection

from . import audit
from .appeals.router import router as appeals_router
from .assistant.router import router as assistant_router
from .auth import DEMO_USERS, User, current_user
from .cases.router import router as cases_router
from .config import get_settings
from .db import get_conn, rows
from .law.router import router as law_router
from .rawfiles import router as raw_router
from .admin.router import router as admin_router
from .law.uploads import router as uploads_router

@asynccontextmanager
async def lifespan(_app):
    # งานสร้าง embedding index ที่ค้างจาก process เดิมถือว่าถูกขัดจังหวะ (ผู้ดูแลกดอัปเดตเพื่อทำต่อ)
    from .db import get_engine
    from .law import uploads
    from .search import vector_index
    try:
        with get_engine().begin() as c:
            vector_index.mark_interrupted(c)
            uploads.mark_interrupted(c)
    except Exception:  # noqa: BLE001 — ฐานข้อมูลยังไม่พร้อม/ยังไม่ได้ seed
        pass
    yield


app = FastAPI(title="ERC Legal & Case Platform — POC", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["X-Request-Id"])


@app.exception_handler(HTTPException)
async def problem_json(request: Request, exc: HTTPException):
    """รูปแบบ error ตาม SDD 4.3 (problem+json)"""
    d = exc.detail if isinstance(exc.detail, dict) else {"code": "ERROR", "detail": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, media_type="application/problem+json",
                        content={"status": exc.status_code, "title": d.get("code"), **d})


@app.exception_handler(RequestValidationError)
async def validation_problem(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, media_type="application/problem+json",
                        content={"status": 400, "code": "VALIDATION_FAILED", "detail": exc.errors()[:3]})


for r in (uploads_router, law_router, appeals_router, cases_router, assistant_router, raw_router, admin_router):
    app.include_router(r)


@app.get("/api/v1/health")
def health():
    return {"ok": True}


@app.get("/api/v1/me")
def me(user: User = Depends(current_user)):
    return {**user.__dict__, "demo_users": [u.__dict__ for u in DEMO_USERS.values()]}


@app.get("/api/v1/audit-events")
def audit_events(limit: int = 50, user: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    if user.role != "ADM":
        raise HTTPException(403, detail={"code": "FORBIDDEN_ACTION", "detail": "เฉพาะผู้ดูแลระบบ"})
    ok, broken = audit.verify_chain(conn)
    items = rows(conn, """SELECT seq, occurred_at, actor_id, actor_role, action, resource, outcome, detail
                          FROM audit_event ORDER BY seq DESC LIMIT :n""", n=min(limit, 500))
    return {"chain_valid": ok, "broken_at": broken, "items": items}


# โหมด deploy ชิ้นเดียว: ถ้ามีหน้าเว็บที่ build แล้ว (WEB_DIST) ให้ FastAPI เสิร์ฟด้วย
_dist = Path(os.getenv("WEB_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if (_dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")
    if (_dist / "brand").is_dir():  # โลโก้สำนักงาน (frontend/public/brand)
        app.mount("/brand", StaticFiles(directory=_dist / "brand"), name="brand")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(_dist / "index.html")
