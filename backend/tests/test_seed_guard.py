"""seed โหมดสาธิตต้องไม่ลบข้อมูลจริง; --laws-only นำเข้าตัวบทใหม่โดยไม่แตะงานอุทธรณ์/คดี/audit"""

import pytest
from sqlalchemy import text


@pytest.fixture()
def conn(seeded_client):
    from app.db import get_engine
    with get_engine().connect() as c:
        tx = c.begin()
        yield c
        tx.rollback()


def test_demo_seed_refused_in_production(conn, monkeypatch):
    from app.seed import seed
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(seed.SeedRefused):
        seed.run(conn)


def test_demo_seed_refused_when_real_appeals_exist(conn):
    from app.seed import seed
    conn.execute(text("UPDATE appeal SET is_demo = FALSE WHERE appeal_id = (SELECT min(appeal_id) FROM appeal)"))
    with pytest.raises(seed.SeedRefused):
        seed.run(conn)


def test_laws_only_keeps_appeals_cases_and_audit(conn, monkeypatch):
    from app.seed import seed
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REGISTER_XLSX", "/nonexistent.xlsx")
    monkeypatch.setenv("REGISTER_PUBLIC_JSON", "/nonexistent.json")
    conn.execute(text("UPDATE appeal SET is_demo = FALSE WHERE appeal_id = (SELECT min(appeal_id) FROM appeal)"))
    before = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
              for t in ("appeal", "court_case", "citation_link", "audit_event")}
    n_inst = conn.execute(text("SELECT count(*) FROM legal_instrument")).scalar()
    out = seed.run_laws(conn)
    after = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in before}
    assert after == before
    assert out["legal_instrument"] == n_inst and out["law_chunks"] > 0
    assert conn.execute(text("SELECT count(*) FROM search_chunk WHERE kind = 'appeal'")).scalar() > 0
