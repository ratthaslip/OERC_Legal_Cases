import json
import logging
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import User, current_user
from ..config import get_settings
from ..db import get_engine
from .rag import answer

log = logging.getLogger("erc.assistant")

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    as_of: date | None = None


@router.get("/status")
def status():
    s = get_settings()
    return {"enabled": s.assistant_enabled, "provider": s.llm_provider,
            "model": None if s.llm_provider == "extractive" else s.llm_model}


@router.post("/answers")
def create_answer(q: Question, user: User = Depends(current_user)):
    """Server-Sent Events: sources → sentence (ทีละประโยคที่ผ่านการตรวจ) → done"""

    def events():
        # ใช้ connection ของตัวเอง เพราะ generator ทำงานต่อหลัง request handler คืนค่าแล้ว
        try:
            with get_engine().begin() as conn:
                for name, data in answer(conn, user, q.question, q.as_of):
                    yield f"event: {name}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001 — ส่ง error เป็น event แทนการตัด stream กลางทาง
            log.exception("assistant stream failed")
            err = {"code": "ASSISTANT_FAILED", "detail": f"{type(e).__name__}: {str(e)[:200]}"}
            yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
