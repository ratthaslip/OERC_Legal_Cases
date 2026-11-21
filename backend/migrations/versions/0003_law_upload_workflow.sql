-- ขั้นตอนนำเข้าตัวบท: ร่าง → ส่งตรวจ → อนุมัติ, เก็บทุกรุ่นของร่าง, คำขอแก้ไขฉบับที่เผยแพร่แล้ว
-- status: processing | draft | submitted | approved | rejected | failed | superseded (ฉบับที่ถูกคำขอแก้ไขแทนที่)
ALTER TABLE law_upload ADD COLUMN IF NOT EXISTS submitted_by VARCHAR(40);
ALTER TABLE law_upload ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
ALTER TABLE law_upload ADD COLUMN IF NOT EXISTS amends_eli  VARCHAR(200);   -- คำขอแก้ไขตัวบทที่เผยแพร่แล้ว
ALTER TABLE law_upload ADD COLUMN IF NOT EXISTS amends_upload INT;          -- รายการนำเข้าเดิมที่ถูกแก้
CREATE UNIQUE INDEX IF NOT EXISTS law_upload_one_open_correction ON law_upload (amends_eli)
    WHERE amends_eli IS NOT NULL AND status IN ('draft', 'submitted');

-- ทุกครั้งที่ระบบถอดข้อความหรือผู้ใช้บันทึกร่าง เก็บร่างทั้งฉบับเป็นรุ่นใหม่ (แก้/ลบไม่ได้)
-- ไม่มี FK ไป law_upload โดยตั้งใจ: ลบรายการนำเข้าแล้วประวัติยังอยู่ตามหลักไม่ลบหลักฐาน
-- kind: extract (ถอดข้อความ) | reprocess (ถอดใหม่) | correction (ตั้งต้นจากฉบับที่เผยแพร่) | edit (ผู้ใช้แก้)
CREATE TABLE IF NOT EXISTS law_upload_revision (
    rev_id     SERIAL PRIMARY KEY,
    upload_id  INT NOT NULL,
    rev_no     INT NOT NULL,
    kind       VARCHAR(12) NOT NULL CHECK (kind IN ('extract', 'reprocess', 'correction', 'edit')),
    draft      JSONB NOT NULL,
    actor      VARCHAR(40) NOT NULL,
    summary    JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (upload_id, rev_no)
);

-- ข้อยกเว้นเดียว: ลบได้เมื่อ SET LOCAL app.purge_internal = 'on' (seed/public_only.py purge บนเครื่อง PUBLIC_DATA_ONLY)
CREATE OR REPLACE FUNCTION law_upload_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('app.purge_internal', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION USING MESSAGE = 'law_upload_revision is append-only: ' || TG_OP || ' not allowed', ERRCODE = 'insufficient_privilege';
END $$;
DROP TRIGGER IF EXISTS law_upload_revision_no_update ON law_upload_revision;
CREATE TRIGGER law_upload_revision_no_update BEFORE UPDATE OR DELETE ON law_upload_revision
    FOR EACH ROW EXECUTE FUNCTION law_upload_revision_immutable();
DROP TRIGGER IF EXISTS law_upload_revision_no_truncate ON law_upload_revision;
CREATE TRIGGER law_upload_revision_no_truncate BEFORE TRUNCATE ON law_upload_revision
    FOR EACH STATEMENT EXECUTE FUNCTION law_upload_revision_immutable();
