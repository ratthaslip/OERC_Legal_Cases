-- เพิ่ม/แก้ไขคดีจากหน้าเว็บ, นำเข้าทะเบียนคดีจาก Excel, แนบคำพิพากษา/คำสั่งศาล
ALTER TABLE court_case ADD COLUMN IF NOT EXISTS row_version INT NOT NULL DEFAULT 1;
ALTER TABLE court_case ADD COLUMN IF NOT EXISTS created_by  VARCHAR(40);
ALTER TABLE court_case ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE court_case ADD COLUMN IF NOT EXISTS updated_by  VARCHAR(40);
ALTER TABLE court_case ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ;

-- เอกสารที่แนบจากหน้าเว็บ: ไฟล์ต้นฉบับอยู่ใน raw_file (sha256), ข้อความที่ถอดได้อยู่ใน excerpt (ใช้ค้นหา)
ALTER TABLE case_document ADD COLUMN IF NOT EXISTS sha256        CHAR(64);
ALTER TABLE case_document ADD COLUMN IF NOT EXISTS original_name TEXT;
ALTER TABLE case_document ADD COLUMN IF NOT EXISTS status        VARCHAR(12) NOT NULL DEFAULT 'ready';  -- processing | ready | failed
ALTER TABLE case_document ADD COLUMN IF NOT EXISTS method        VARCHAR(8);    -- docx | doc | pdf | ocr
ALTER TABLE case_document ADD COLUMN IF NOT EXISTS error         TEXT;
ALTER TABLE case_document ADD COLUMN IF NOT EXISTS added_by      VARCHAR(40);
ALTER TABLE case_document ADD COLUMN IF NOT EXISTS added_at      TIMESTAMPTZ;

-- ภาพรวมของคดีหลังแก้ไขทุกครั้ง (แก้/ลบไม่ได้) — ใช้ดูว่าใครแก้อะไรเมื่อไร
-- kind: create | edit | proceeding | import
CREATE TABLE IF NOT EXISTS court_case_revision (
    rev_id     SERIAL PRIMARY KEY,
    case_id    INT NOT NULL,
    rev_no     INT NOT NULL,
    kind       VARCHAR(12) NOT NULL CHECK (kind IN ('create', 'edit', 'proceeding', 'import')),
    snapshot   JSONB NOT NULL,
    actor      VARCHAR(40) NOT NULL,
    summary    JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, rev_no)
);
CREATE OR REPLACE FUNCTION court_case_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = 'court_case_revision is append-only: ' || TG_OP || ' not allowed', ERRCODE = 'insufficient_privilege';
END $$;
DROP TRIGGER IF EXISTS court_case_revision_no_update ON court_case_revision;
CREATE TRIGGER court_case_revision_no_update BEFORE UPDATE OR DELETE ON court_case_revision
    FOR EACH ROW EXECUTE FUNCTION court_case_revision_immutable();
DROP TRIGGER IF EXISTS court_case_revision_no_truncate ON court_case_revision;
CREATE TRIGGER court_case_revision_no_truncate BEFORE TRUNCATE ON court_case_revision
    FOR EACH STATEMENT EXECUTE FUNCTION court_case_revision_immutable();

-- นำเข้าทะเบียนคดีจาก Excel: อัปโหลด → ตรวจทีละแถว → ส่งตรวจ → ผู้อื่นอนุมัติ → บันทึกเข้า court_case
-- status: draft | submitted | applied | rejected
CREATE TABLE IF NOT EXISTS case_import (
    import_id     SERIAL PRIMARY KEY,
    original_name TEXT NOT NULL,
    sha256        CHAR(64) NOT NULL,
    status        VARCHAR(12) NOT NULL DEFAULT 'draft',
    cases         JSONB NOT NULL,      -- แถวที่อ่านได้ พร้อมผลตรวจ (errors / warnings / action)
    proceedings   JSONB NOT NULL DEFAULT '[]',
    error_count   INT NOT NULL DEFAULT 0,
    created_by    VARCHAR(40) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_by  VARCHAR(40),
    submitted_at  TIMESTAMPTZ,
    reviewed_by   VARCHAR(40),
    reviewed_at   TIMESTAMPTZ,
    review_note   TEXT,
    result        JSONB
);
