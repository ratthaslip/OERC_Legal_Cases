"""ส่งสำเนา audit log ออกนอกฐานข้อมูล — หลัง transaction commit เท่านั้น (เหตุการณ์ที่ rollback ไม่ถูกส่ง)

AUDIT_SINK (คั่นหลายปลายทางด้วย ,):
  log                       logger "erc.audit" → stdout (JSON) → ระบบรวม log ของสำนักงาน (ค่าเริ่ม)
  file:/data/audit/audit.jsonl   ต่อท้ายไฟล์ (บรรทัดละเหตุการณ์) — ควรอยู่บนดิสก์/NFS แยกจากฐานข้อมูล
  syslog://host:514         syslog UDP;  syslog+tcp://host:514  syslog TCP (เช่น SIEM / WORM storage)
  none                      ไม่ส่ง
ส่งไม่สำเร็จไม่ทำให้คำขอล้ม แต่บันทึก error ใน log; ตรวจความครบด้วย `python -m app.ops audit-verify --file …`
"""
import json
import logging
import logging.handlers
import os
import socket
import threading
from urllib.parse import urlparse

log = logging.getLogger("erc.audit_sink")
audit_log = logging.getLogger("erc.audit")
_lock = threading.Lock()
_syslog: dict[str, logging.Handler] = {}


def _targets() -> list[str]:
    raw = os.environ.get("AUDIT_SINK", "log")
    return [t.strip() for t in raw.split(",") if t.strip() and t.strip() != "none"]


def _syslog_handler(url: str) -> logging.Handler:
    if url not in _syslog:
        u = urlparse(url)
        tcp = u.scheme == "syslog+tcp"
        h = logging.handlers.SysLogHandler(address=(u.hostname or "localhost", u.port or 514),
                                           facility=logging.handlers.SysLogHandler.LOG_AUTH,
                                           socktype=socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
        h.setFormatter(logging.Formatter("erc-audit: %(message)s"))
        _syslog[url] = h
    return _syslog[url]


def line(ev: dict) -> str:
    return json.dumps(ev, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def emit(events: list[dict]) -> None:
    for t in _targets():
        try:
            if t == "log":
                for ev in events:
                    audit_log.info("audit %s %s", ev["action"], ev["resource"], extra={"audit": ev})
            elif t.startswith("file:"):
                path = t[5:]
                with _lock:
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    with open(path, "a", encoding="utf-8") as f:
                        for ev in events:
                            f.write(line(ev) + "\n")
                        f.flush()
                        os.fsync(f.fileno())
            elif t.startswith("syslog"):
                h = _syslog_handler(t)
                for ev in events:
                    h.emit(logging.makeLogRecord({"msg": line(ev), "levelno": logging.INFO, "levelname": "INFO"}))
            else:
                log.error("AUDIT_SINK ไม่รู้จัก: %s", t)
        except Exception:
            log.exception("ส่งสำเนา audit log ไป %s ไม่สำเร็จ (seq %s–%s)", t, events[0].get("seq"), events[-1].get("seq"))


# ---------------------------------------------------------------- ผูกกับ transaction
PENDING = "audit_pending"


def install(engine) -> None:
    """ส่งเหตุการณ์ที่ค้างใน connection เมื่อ commit; ทิ้งเมื่อ rollback"""
    from sqlalchemy import event

    @event.listens_for(engine, "commit")
    def _on_commit(conn):
        pending = conn.info.pop(PENDING, None)
        if pending:
            emit(pending)

    @event.listens_for(engine, "rollback")
    def _on_rollback(conn):
        conn.info.pop(PENDING, None)
