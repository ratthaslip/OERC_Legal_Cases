import os
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

app = FastAPI(title="ERC Legal & Case Platform — POC", version="0.1.0")
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


for r in (law_router, appeals_router, cases_router, assistant_router):
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

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(_dist / "index.html")
