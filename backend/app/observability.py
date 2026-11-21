"""Log แบบมีโครงสร้าง (JSON บรรทัดละเหตุการณ์) + request ID ต่อคำขอ

- ทุกคำขอได้ X-Request-Id (รับจาก proxy ถ้ารูปแบบถูกต้อง ไม่งั้นสร้างใหม่) ส่งกลับใน header และอยู่ในทุกบรรทัด log
- บรรทัด access log หนึ่งบรรทัดต่อคำขอ: method, path (ไม่บันทึก query string — อาจมีคำค้นที่เป็นข้อมูลส่วนบุคคล), status,
  เวลา, ผู้ใช้, IP
- LOG_FORMAT=json | text (ค่าเริ่ม: json เมื่อ APP_ENV=production), LOG_LEVEL=INFO
"""
import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, UTC

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
access_log = logging.getLogger("erc.access")
_RID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
             "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
             "request_id": getattr(record, "request_id", None) or request_id_var.get()}
        d.update({k: v for k, v in vars(record).items() if k not in _STD and k not in d})
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


def setup_logging() -> None:
    from .config import is_production
    fmt = os.environ.get("LOG_FORMAT", "json" if is_production() else "text").lower()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    # ให้ log ของ uvicorn ใช้รูปแบบเดียวกัน; access log ของ uvicorn ปิด (ใช้ erc.access แทน)
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers, lg.propagate = [], True
    logging.getLogger("uvicorn.access").disabled = True


class RequestContextMiddleware:
    """ASGI middleware (ไม่บัฟเฟอร์ response จึงใช้กับ SSE ได้)"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rid = headers.get("x-request-id", "")
        rid = rid if _RID_OK.match(rid) else uuid.uuid4().hex
        request_id_var.set(rid)
        status = {"code": 500}
        t0 = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            client = scope.get("client") or ("-", 0)
            quiet = scope.get("path") == "/api/v1/health" and status["code"] == 200   # health check ทุก 30 วินาที
            access_log.log(logging.DEBUG if quiet else logging.INFO, "%s %s %s", scope.get("method"), scope.get("path"),
                           status["code"], extra={
                    "method": scope.get("method"), "path": scope.get("path"), "status": status["code"],
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1), "client_ip": client[0],
                    "user": headers.get("x-demo-user") or "anonymous",   # ระบบจริง: subject จาก token OIDC
                    "query_len": len(scope.get("query_string") or b"")})
