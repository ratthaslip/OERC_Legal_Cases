-- ผู้ใช้ฐานข้อมูลของแอปที่ไม่ใช่เจ้าของตาราง (ใช้เมื่อฐานข้อมูลอยู่บน VM DB แยก)
--   psql -U postgres -d erc -v app_password="'<รหัสยาว>'" -f deploy/db/app-role.sql
-- แอปใช้: EXTERNAL_DATABASE_URL=postgresql+psycopg://erc_app:<รหัส>@<VM DB>:5432/erc
-- migration / นำเข้าตัวบท (seed --laws-only) ใช้เจ้าของตาราง: MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:…@…/erc
--   docker compose -f docker-compose.prod.yml exec -e DATABASE_URL="$MIGRATION_DATABASE_URL" api python -m app.seed.seed --laws-only

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erc_app') THEN
        CREATE ROLE erc_app LOGIN;
    END IF;
END $$;
ALTER ROLE erc_app PASSWORD :app_password;

GRANT CONNECT ON DATABASE erc TO erc_app;
GRANT USAGE ON SCHEMA public TO erc_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO erc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO erc_app;
-- audit log: เพิ่มได้อย่างเดียว (trigger กันอีกชั้น)
REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM erc_app;
-- ตารางที่สร้างภายหลังโดย migration ได้สิทธิ์เดียวกัน
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO erc_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO erc_app;
