import os

import pytest

# ฐานข้อมูลทดสอบ: เทสต์รับเรื่องอุทธรณ์ผ่าน API (ไม่ใช่ข้อมูลสาธิต) แล้ว seed ใหม่ในโมดูลถัดไป
os.environ.setdefault("SEED_ALLOW_DATA_LOSS", "1")


@pytest.fixture(scope="module")
def seeded_client():
    """TestClient บนฐานข้อมูลทดสอบที่ seed ข้อมูลสาธิตใหม่ (ข้ามถ้าไม่ได้ตั้ง TEST_DATABASE_URL)"""
    db = os.environ.get("TEST_DATABASE_URL")
    if not db:
        pytest.skip("ตั้ง TEST_DATABASE_URL เพื่อรันเทสต์ API")
    os.environ.update(DATABASE_URL=db, LLM_PROVIDER="extractive", EMBED_PROVIDER="hash")
    os.environ.pop("REGISTER_XLSX", None)
    from app.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.db import get_engine
    from app.main import app
    from app.seed import seed
    with get_engine().begin() as conn:
        seed.run(conn)
    return TestClient(app)
