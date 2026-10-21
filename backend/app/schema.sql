-- POC schema: PostgreSQL เดียว แยกตารางตามโมดูล (SDD หัวข้อ 3 ฉบับย่อ)
-- ใน POC ใช้ฐานเดียวเพื่อลดงาน ops; ขอบเขตตารางยังตรงกับ law_db / case_db / ops_db เพื่อแยกฐานได้ภายหลัง
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ---------- law ----------
CREATE TABLE IF NOT EXISTS legal_instrument (
    instrument_id   SERIAL PRIMARY KEY,
    eli_id          VARCHAR(200) NOT NULL UNIQUE,
    instrument_type VARCHAR(30)  NOT NULL,          -- act | royal_decree | regulation | notification
    title_th        TEXT         NOT NULL,
    short_title     VARCHAR(100),
    issuer          TEXT,
    category        VARCHAR(60),
    promulgated_date DATE,
    effective_date  DATE,
    gazette_ref     VARCHAR(200),
    is_demo         BOOLEAN NOT NULL DEFAULT FALSE  -- TRUE = ข้อความสาธิต ไม่ใช่ตัวบทจริง
);

CREATE TABLE IF NOT EXISTS provision (
    provision_id  SERIAL PRIMARY KEY,
    instrument_id INT NOT NULL REFERENCES legal_instrument(instrument_id),
    eli_id        VARCHAR(200) NOT NULL UNIQUE,
    provision_no  VARCHAR(20)  NOT NULL,
    part_heading  TEXT,
    sort_order    INT NOT NULL DEFAULT 0,
    UNIQUE (instrument_id, provision_no)
);

CREATE TABLE IF NOT EXISTS provision_version (
    version_id    SERIAL PRIMARY KEY,
    provision_id  INT NOT NULL REFERENCES provision(provision_id),
    version_no    INT NOT NULL,
    body_text     TEXT NOT NULL,
    valid_from    DATE NOT NULL,
    valid_to      DATE,                                 -- NULL = รุ่นปัจจุบัน (ไม่ใช้ 9999-12-31)
    status        VARCHAR(20) NOT NULL DEFAULT 'approved', -- draft | approved | withdrawn
    amended_by    TEXT,
    summary       TEXT,
    UNIQUE (provision_id, version_no),
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    -- ห้ามช่วงวันที่มีผลของรุ่นที่อนุมัติแล้วซ้อนกัน → resolver ได้ไม่เกิน 1 แถวเสมอ
    EXCLUDE USING gist (provision_id WITH =, daterange(valid_from, valid_to, '[]') WITH &&)
        WHERE (status = 'approved')
);

CREATE TABLE IF NOT EXISTS legal_relation (
    relation_id   SERIAL PRIMARY KEY,
    version_id    INT NOT NULL REFERENCES provision_version(version_id),
    relation_type TEXT NOT NULL,     -- แก้ไขเพิ่มเติมจาก | ถูกแก้ไขโดย | ออกโดยอาศัยอำนาจตาม | ฐานอำนาจของ
    target_text   TEXT NOT NULL,
    target_eli_id VARCHAR(200)
);

-- ---------- appeals ----------
-- ขั้นตอนตามคู่มือปฏิบัติงาน WI-EFL-04 (ข้อ 5 และตารางกำหนดระยะเวลาข้อ 6) — แก้ที่ตารางนี้ได้โดยไม่ต้องแก้โค้ด
CREATE TABLE IF NOT EXISTS workflow_step_def (
    step_no      INT PRIMARY KEY,
    code         VARCHAR(20) NOT NULL UNIQUE,
    name_th      TEXT NOT NULL,
    optional     BOOLEAN NOT NULL DEFAULT FALSE,  -- ข้ามได้ (ขั้น 3 แจ้งแก้ไข, ขั้น 5 เสนอ กกพ. โดยตรง)
    anchors      TEXT[] NOT NULL,                 -- milestone ที่ใช้เริ่มนับ (ใช้ตัวแรกที่มี)
    limit_days   INT NOT NULL,
    day_type     VARCHAR(2) NOT NULL,             -- BD = วันทำการ, CD = วันปฏิทิน
    limit_text   TEXT NOT NULL,
    basis        TEXT
);

-- วันหยุดราชการ (ใช้นับวันทำการ) — โหลดจากประกาศวันหยุดประจำปีของ ครม.
CREATE TABLE IF NOT EXISTS holiday (
    holiday_date DATE PRIMARY KEY,
    name_th      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appeal (
    appeal_id       SERIAL PRIMARY KEY,
    appeal_no       VARCHAR(50) NOT NULL,
    appeal_no_norm  VARCHAR(50) NOT NULL UNIQUE,
    case_type       VARCHAR(10) NOT NULL DEFAULT 'APPEAL',  -- APPEAL (อุทธรณ์ ม.121) | COMPLAINT (เรื่องร้องเรียน)
    complaint_basis VARCHAR(20),                 -- PROCUREMENT (ระเบียบจัดหา/ประกาศรับซื้อไฟฟ้า) | PPA (สัญญาซื้อขายไฟฟ้า)
    target_type     VARCHAR(10),                 -- ORDER (คำสั่ง) | RULE (ระเบียบ ข้อบังคับ ประกาศ ข้อกำหนด)
    esarabun_no     VARCHAR(50),                 -- เลขรับระบบ E-Sarabun
    register_no     VARCHAR(50),                 -- เลขทะเบียนคุมงาน FM-EFL-01
    filing_channel  VARCHAR(12),                 -- COUNTER | POST | ELECTRONIC
    subject         TEXT NOT NULL,
    summary         TEXT NOT NULL,
    party_code      VARCHAR(20) NOT NULL,        -- ชื่อจริงไม่อยู่ในตารางนี้ (pseudonymized)
    party_label     TEXT NOT NULL,
    respondent      TEXT,
    energy_sector   VARCHAR(40) NOT NULL,
    incident_date   DATE,
    notice_date     DATE,                        -- วันได้รับแจ้งคำสั่ง / วันที่กฎประกาศใช้บังคับ
    received_date   DATE,                        -- วันยื่นอุทธรณ์ตามข้อ 5.2(3): วันยื่น / วันประทับตราไปรษณีย์ต้นทาง / วันได้รับทางอิเล็กทรอนิกส์
    sla_days        INT,                         -- อุทธรณ์ = 90 (ม.49 พ.ร.บ.จัดตั้งศาลปกครองฯ); ร้องเรียน = ตามระเบียบ/ประกาศ/สัญญา
    screening       JSONB,                       -- ผลตรวจสิทธิ เขตอำนาจ ระยะเวลา และเอกสาร
    admissibility   VARCHAR(10) NOT NULL DEFAULT 'PENDING',  -- PENDING | ACCEPTED | REJECTED
    route           VARCHAR(12),                 -- SUBCOMMITTEE | DIRECT (เสนอ กกพ. โดยไม่ผ่านอนุฯ)
    route_reason    TEXT,
    erc_meeting_ref VARCHAR(50),
    status          VARCHAR(20) NOT NULL DEFAULT 'IN_PROGRESS',  -- IN_PROGRESS | CLOSED
    decision_result TEXT,
    classification  VARCHAR(20) NOT NULL DEFAULT 'internal',
    row_version     INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS appeal_owner (
    appeal_id INT NOT NULL REFERENCES appeal(appeal_id),
    user_id   VARCHAR(30) NOT NULL,
    PRIMARY KEY (appeal_id, user_id)
);

CREATE TABLE IF NOT EXISTS appeal_step (
    appeal_id    INT NOT NULL REFERENCES appeal(appeal_id),
    step_no      INT NOT NULL REFERENCES workflow_step_def(step_no),
    state        VARCHAR(10) NOT NULL DEFAULT 'pending',  -- pending | current | completed | skipped
    started_at   DATE,
    completed_at DATE,
    note         TEXT,
    PRIMARY KEY (appeal_id, step_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS appeal_one_current ON appeal_step (appeal_id) WHERE state = 'current';

-- เหตุการณ์ที่มีวันที่ (ใช้เป็นจุดเริ่มนับของแต่ละกรอบเวลา) — หนึ่ง code ต่อเรื่อง
CREATE TABLE IF NOT EXISTS appeal_milestone (
    appeal_id  INT NOT NULL REFERENCES appeal(appeal_id),
    code       VARCHAR(30) NOT NULL,   -- RECEIVED, ASSIGNED, SCREEN_DONE, CORRECTION_DONE, FACTS_DONE, SUBC_FIRST_MEETING,
                                       -- SUBC_EXT_NOTICE, SUBC_EXTENDED_DUE, SUBC_REPORT, ERC_AGENDA_SENT, ERC_RECEIVED,
                                       -- ERC_RESOLUTION, NOTIFY_SENT, NOTIFY_RECEIVED, CLOSED
    at_date    DATE NOT NULL,
    note       TEXT,
    actor_id   VARCHAR(30),
    PRIMARY KEY (appeal_id, code)
);

-- หนังสือแจ้งแก้ไข / ขอข้อเท็จจริงและเอกสาร (ข้อ 5.2(2), 5.4)
CREATE TABLE IF NOT EXISTS appeal_request (
    request_id     SERIAL PRIMARY KEY,
    appeal_id      INT NOT NULL REFERENCES appeal(appeal_id),
    step_no        INT NOT NULL,
    kind           VARCHAR(12) NOT NULL,   -- CORRECTION | INFO
    target_type    VARCHAR(10) NOT NULL,   -- APPELLANT | INTERNAL | EXTERNAL
    target_name    TEXT NOT NULL,
    subject        TEXT NOT NULL,
    doc_no         VARCHAR(50),
    issued_date    DATE NOT NULL,
    due_date       DATE NOT NULL,
    responded_date DATE,
    note           TEXT
);

-- ---------- court cases ----------
CREATE TABLE IF NOT EXISTS court_case (
    case_id        SERIAL PRIMARY KEY,
    appeal_id      INT REFERENCES appeal(appeal_id),
    black_no       VARCHAR(50) NOT NULL,
    red_no         VARCHAR(50),
    court_level    VARCHAR(50) NOT NULL,     -- ศาลปกครองชั้นต้น | ศาลปกครองสูงสุด
    court_name     VARCHAR(100) NOT NULL,
    case_category  VARCHAR(30) NOT NULL DEFAULT 'คดีปกครอง',
    subject        TEXT NOT NULL,
    summary        TEXT NOT NULL,
    party_code     VARCHAR(20) NOT NULL,
    party_label    TEXT NOT NULL,
    defendant      TEXT,
    energy_sector  VARCHAR(40) NOT NULL,
    incident_date  DATE,
    filed_date     DATE,
    judgment_date  DATE,
    status         VARCHAR(30) NOT NULL,     -- PENDING | FINAL
    stage_text     TEXT,
    result         TEXT,
    classification VARCHAR(20) NOT NULL DEFAULT 'internal',
    UNIQUE (court_name, black_no)
);

CREATE TABLE IF NOT EXISTS court_case_owner (
    case_id INT NOT NULL REFERENCES court_case(case_id),
    user_id VARCHAR(30) NOT NULL,
    PRIMARY KEY (case_id, user_id)
);

CREATE TABLE IF NOT EXISTS court_proceeding (
    proceeding_id   SERIAL PRIMARY KEY,
    case_id         INT NOT NULL REFERENCES court_case(case_id),
    proceeding_date DATE NOT NULL,
    title           TEXT NOT NULL,
    detail          TEXT
);

CREATE TABLE IF NOT EXISTS case_document (
    document_id SERIAL PRIMARY KEY,
    owner_type  VARCHAR(12) NOT NULL CHECK (owner_type IN ('appeal', 'court_case')),
    owner_id    INT NOT NULL,
    doc_no      VARCHAR(40),
    doc_type    TEXT NOT NULL,
    doc_date    DATE,
    pages       INT,
    excerpt     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS case_document_owner ON case_document (owner_type, owner_id);

-- ---------- citations (POC: ใช้ชุดที่ยืนยันแล้ว ยังไม่มีคิวตรวจทาน) ----------
CREATE TABLE IF NOT EXISTS citation_link (
    citation_id      SERIAL PRIMARY KEY,
    owner_type       VARCHAR(12) NOT NULL CHECK (owner_type IN ('appeal', 'court_case')),
    owner_id         INT NOT NULL,
    cited_eli_id     VARCHAR(200) NOT NULL,   -- eli ของ provision; ไม่มี FK ข้ามโมดูลโดยตั้งใจ
    cited_version_no INT NOT NULL,
    state            VARCHAR(20) NOT NULL DEFAULT 'verified',
    note             TEXT
);
CREATE INDEX IF NOT EXISTS citation_by_eli ON citation_link (cited_eli_id);

-- ---------- search (POC: Postgres FTS + embedding เก็บเป็น real[]) ----------
CREATE TABLE IF NOT EXISTS search_chunk (
    chunk_id       SERIAL PRIMARY KEY,
    kind           VARCHAR(12) NOT NULL,      -- provision | appeal | court_case
    ref_id         INT NOT NULL,              -- version_id | appeal_id | case_id
    eli_id         VARCHAR(200),
    version_no     INT,
    valid_from     DATE,
    valid_to       DATE,
    title          TEXT NOT NULL,
    chunk_text     TEXT NOT NULL,
    tokens         TSVECTOR NOT NULL,
    embedding      REAL[] NOT NULL,
    classification VARCHAR(20) NOT NULL DEFAULT 'public',
    owners         TEXT[] NOT NULL DEFAULT '{}',
    energy_sector  VARCHAR(40)
);
CREATE INDEX IF NOT EXISTS search_chunk_tokens ON search_chunk USING gin (tokens);
CREATE INDEX IF NOT EXISTS search_chunk_kind ON search_chunk (kind, ref_id);

-- ---------- audit (append-only + hash chain) ----------
CREATE TABLE IF NOT EXISTS audit_event (
    seq         BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id    VARCHAR(30) NOT NULL,
    actor_role  VARCHAR(10) NOT NULL,
    action      VARCHAR(60) NOT NULL,
    resource    VARCHAR(200) NOT NULL,
    outcome     VARCHAR(10) NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}',
    prev_hash   CHAR(64) NOT NULL,
    hash        CHAR(64) NOT NULL
);
