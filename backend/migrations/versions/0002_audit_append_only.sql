-- audit log แก้/ลบไม่ได้ (ผู้ใช้ของแอปทำได้แค่เพิ่มแถว) — DROP TABLE ยังทำได้สำหรับล้างฐานทดสอบ/สาธิต
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = 'audit_event is append-only: ' || TG_OP || ' not allowed', ERRCODE = 'insufficient_privilege';
END $$;
DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event;
CREATE TRIGGER audit_event_no_update BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event;
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION audit_event_immutable();
