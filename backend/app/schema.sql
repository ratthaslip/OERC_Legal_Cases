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
    is_demo         BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE = ข้อความสาธิต ไม่ใช่ตัวบทจริง
    register_title  TEXT,                           -- ชื่อตามทะเบียนกฎหมายลำดับรอง (ใช้ผูกทะเบียน ↔ ตัวบท)
    classification  VARCHAR(20) NOT NULL DEFAULT 'public'  -- public | internal (ระเบียบบริหารภายใน: เห็นเฉพาะเจ้าหน้าที่)
);
ALTER TABLE legal_instrument ADD COLUMN IF NOT EXISTS register_title TEXT;
ALTER TABLE legal_instrument ADD COLUMN IF NOT EXISTS classification VARCHAR(20) NOT NULL DEFAULT 'public';

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
    relation_type TEXT NOT NULL,     -- (บันทึกตามตัวบทต้นฉบับ; ใช้งานผ่าน law_relation) แก้ไขเพิ่มเติมจาก | ถูกแก้ไขโดย | ออกโดยอาศัยอำนาจตาม | ฐานอำนาจของ
    target_text   TEXT NOT NULL,
    target_eli_id VARCHAR(200)
);


-- ความสัมพันธ์ระหว่างกฎหมายแบบรวมศูนย์ (เส้นเชื่อมของ "กราฟกฎหมาย" ใน PostgreSQL — เดินหลายทอดด้วย recursive CTE)
-- ทิศทางเดียวกันทุกประเภท: src → dst  เช่น  มาตรา 121 —authorizes→ ระเบียบอุทธรณ์ / ฉบับแก้ไข —amends→ ข้อ 21
-- สร้างใหม่ทุกครั้งที่นำเข้า (app/law/relations.py: rebuild) ยกเว้น source = 'manual' ที่นิติกรบันทึกเอง
CREATE TABLE IF NOT EXISTS law_relation (
    relation_id     SERIAL PRIMARY KEY,
    rel_type        VARCHAR(20) NOT NULL CHECK (rel_type IN ('authorizes', 'amends', 'repeals', 'refers_to', 'remedy')),
    src_eli         VARCHAR(200),                      -- ต้นทาง (NULL = ยังผูกกับตัวบทในระบบไม่ได้ ดู src_text)
    src_text        TEXT,
    dst_eli         VARCHAR(200),                      -- ปลายทาง: มาตรา/ข้อ หรือตราสาร
    dst_register_id INT,                               -- ปลายทางที่มีแค่ในทะเบียน (ยังไม่มีตัวบท)
    dst_text        TEXT,
    valid_from      DATE,
    valid_to        DATE,
    status          VARCHAR(10) NOT NULL DEFAULT 'approved' CHECK (status IN ('auto', 'draft', 'approved', 'rejected')),
    source          VARCHAR(20) NOT NULL,              -- register | law_json | text_ref | manual
    source_ref      TEXT,                              -- เช่น ลำดับทะเบียน / version_id / ชื่อไฟล์
    note            TEXT,
    approved_by     VARCHAR(80),
    approved_at     TIMESTAMPTZ,
    CHECK (src_eli IS NOT NULL OR src_text IS NOT NULL),
    CHECK (dst_eli IS NOT NULL OR dst_register_id IS NOT NULL OR dst_text IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS law_relation_src ON law_relation (src_eli, rel_type);
CREATE INDEX IF NOT EXISTS law_relation_dst ON law_relation (dst_eli, rel_type);
CREATE INDEX IF NOT EXISTS law_relation_reg ON law_relation (dst_register_id) WHERE dst_register_id IS NOT NULL;

-- ทะเบียนคุมกฎหมายลำดับรองที่ออกโดยอาศัยอำนาจ พ.ร.บ.การประกอบกิจการพลังงาน พ.ศ. 2550
-- นำเข้าจาก Excel ของฝ่ายกฎหมาย (seed/import_register.py) — 1 แถว Excel = 1 ระเบียน, ยังไม่มีตัวบท
CREATE TABLE IF NOT EXISTS law_register (
    register_id     SERIAL PRIMARY KEY,
    sheet_row       INT NOT NULL UNIQUE,               -- แถวใน Excel (ใช้ย้อนกลับไปแก้ที่ต้นฉบับ)
    reg_no          VARCHAR(20) NOT NULL,              -- "ลำดับ" ตามทะเบียน เช่น 1.7.1 (อาจซ้ำ = ฉบับแก้ไข/ฉบับที่เกี่ยวข้อง)
    group_no        VARCHAR(10) NOT NULL,              -- เลขหลักแรก ใช้จัดกลุ่มเรื่องเดียวกัน
    doc_type        VARCHAR(60),
    title_th        TEXT NOT NULL,
    authority_text  TEXT,                              -- บทอาศัยอำนาจตามที่บันทึก เช่น "11 (1) (4)"
    authority_secs  INT[] NOT NULL DEFAULT '{}',       -- เลขมาตราของ พ.ร.บ. 2550 ที่แยกได้
    owner_unit      VARCHAR(60),                       -- ฝ่ายงานผู้รับผิดชอบ
    effective_text  TEXT,
    effective_date  DATE,                              -- แปลงจาก effective_text (NULL ถ้าแปลงไม่ได้)
    review_text     TEXT,                              -- รอบการประเมินผลสัมฤทธิ์
    status          VARCHAR(12) NOT NULL DEFAULT 'UNKNOWN', -- IN_FORCE | REPEALED | UNKNOWN | GROUP (แถวหัวกลุ่ม)
    status_assumed  BOOLEAN NOT NULL DEFAULT FALSE,    -- TRUE = ทะเบียนไม่ระบุ ระบบถือว่าใช้บังคับไว้ก่อน
    status_text     TEXT,
    note            TEXT,
    hearing_text    TEXT,                              -- รับฟังหลักการ / รับฟังร่าง
    attachments     JSONB NOT NULL DEFAULT '{}',       -- จำนวนเอกสารจากแผ่น "สถิติ"
    issues          TEXT[] NOT NULL DEFAULT '{}',      -- ปัญหาคุณภาพข้อมูลที่ตรวจพบตอนนำเข้า
    instrument_eli  VARCHAR(200),                      -- ผูกกับตัวบทเต็มเมื่อนำเข้าแล้ว
    source_file     TEXT,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS law_register_group ON law_register (group_no);

-- บันทึกการนำเข้าข้อมูลกฎหมาย: ทุกครั้งที่นำเข้าตัวบท (JSON) หรือทะเบียน (Excel) — ตอบได้ว่าข้อมูลมาจากต้นฉบับใด
-- ใคร นำเข้าเมื่อไร ได้กี่มาตรา/รุ่น/แถว (source_sha256 ใช้ตรวจว่าไฟล์ต้นฉบับเปลี่ยนหรือไม่)
CREATE TABLE IF NOT EXISTS law_import_log (
    import_id       SERIAL PRIMARY KEY,
    kind            VARCHAR(20) NOT NULL,              -- law_json | register
    source_name     TEXT NOT NULL,
    source_sha256   CHAR(64) NOT NULL,
    instrument_eli  VARCHAR(200),
    title_th        TEXT,
    provisions      INT,
    versions        INT,
    rows_total      INT,
    rows_linked     INT,
    actor           VARCHAR(80) NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{}',
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS law_import_log_time ON law_import_log (imported_at DESC);

-- ไฟล์ต้นฉบับ (เก็บใน RAW_STORE_DIR ด้วย SHA-256) — ตารางนี้ไม่ถูกล้างเมื่อ seed ใหม่ เพราะเป็นทะเบียนของไฟล์จริง
CREATE TABLE IF NOT EXISTS raw_file (
    sha256          CHAR(64) PRIMARY KEY,
    classification  VARCHAR(10) NOT NULL CHECK (classification IN ('public', 'internal')),
    original_name   TEXT NOT NULL,
    mime            VARCHAR(100) NOT NULL,
    size_bytes      BIGINT NOT NULL,
    pages           INT,
    stored_path     TEXT NOT NULL,                     -- path สัมพัทธ์ใน RAW_STORE_DIR (= key ใน object storage)
    source          TEXT,                              -- แหล่ง เช่น ราชกิจจานุเบกษา, สำนักงานคณะกรรมการกฤษฎีกา, ฝ่ายกฎหมาย กกพ.
    received_from   TEXT,                              -- ผู้ส่ง/ช่องทาง
    received_at     DATE,
    note            TEXT,
    added_by        VARCHAR(80) NOT NULL,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ไฟล์ต้นฉบับใดใช้ทำตัวบทฉบับใด (สร้างจาก "sourceFiles" ใน seed/laws/*.json ทุกครั้งที่ seed)
CREATE TABLE IF NOT EXISTS raw_file_link (
    sha256          CHAR(64) NOT NULL,
    instrument_eli  VARCHAR(200) NOT NULL,
    role            VARCHAR(20) NOT NULL,              -- text = ตัวบทหลัก | amendment = ฉบับแก้ไขที่รวมเข้า
    pages           TEXT,                              -- หน้าที่ใช้ (เช่น "1-22" ในหนังสือรวมกฎหมาย)
    PRIMARY KEY (sha256, instrument_eli)
);

-- ประเด็นค้างของตัวบทที่ต้องให้นิติกรยืนยัน (วันใช้บังคับที่ประมาณ, ฉบับแก้ไขที่ยังไม่ได้รวม ฯลฯ)
-- ที่มา: seed/law_review_notes.json — ปิดประเด็นด้วยการแก้ไฟล์แล้วนำเข้าใหม่
CREATE TABLE IF NOT EXISTS law_open_issue (
    issue_id        SERIAL PRIMARY KEY,
    instrument_eli  VARCHAR(200) NOT NULL,
    provision_eli   VARCHAR(200),
    kind            VARCHAR(30) NOT NULL,              -- date_estimate | pending_amendment | placeholder_text | needs_confirmation
    note            TEXT NOT NULL
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
    is_demo         BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE = เรื่องสมมติที่ seed สร้างเพื่อสาธิต ไม่ใช่เรื่องจริง
    case_type       VARCHAR(10) NOT NULL DEFAULT 'APPEAL',  -- APPEAL (อุทธรณ์ ม.121) | COMPLAINT (เรื่องร้องเรียน)
    complaint_basis VARCHAR(20),                 -- PROCUREMENT (ระเบียบจัดหา/ประกาศรับซื้อไฟฟ้า) | PPA (สัญญาซื้อขายไฟฟ้า) | CONSUMER (ผู้ใช้พลังงาน ระเบียบ 2553)
    appeal_ground   VARCHAR(12),                 -- ฐานการอุทธรณ์ตามข้อ 9 ระเบียบอุทธรณ์ 2552 (appeals/legal.py GROUNDS)
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
    is_demo        BOOLEAN NOT NULL DEFAULT FALSE,   -- TRUE = คดีสมมติเพื่อสาธิต
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
    kind           VARCHAR(12) NOT NULL,      -- provision | register | appeal | court_case
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
    energy_sector  VARCHAR(40),
    is_demo        BOOLEAN NOT NULL DEFAULT FALSE,  -- มาจากเรื่อง/คดีสาธิต (ผู้ช่วย AI ไม่ใช้เป็นหลักฐานโดยค่าเริ่มต้น)
    weight         REAL NOT NULL DEFAULT 1.0        -- น้ำหนักแหล่ง (ลำดับศักดิ์กฎหมาย/กลุ่มผู้ใช้) — indexer.source_weight
);
ALTER TABLE search_chunk ADD COLUMN IF NOT EXISTS weight REAL NOT NULL DEFAULT 1.0;
-- sha256(title + "\n" + chunk_text): ผูกเวกเตอร์จากโมเดลกับเนื้อหา (search_chunk สร้างใหม่ทุกครั้งที่ seed แต่เวกเตอร์ใช้ต่อได้)
ALTER TABLE search_chunk ADD COLUMN IF NOT EXISTS content_sha CHAR(64);
CREATE INDEX IF NOT EXISTS search_chunk_sha ON search_chunk (content_sha);
CREATE INDEX IF NOT EXISTS search_chunk_tokens ON search_chunk USING gin (tokens);
CREATE INDEX IF NOT EXISTS search_chunk_kind ON search_chunk (kind, ref_id);

-- ---------- เอกสารกฎหมายที่เจ้าหน้าที่นำเข้าจากหน้าเว็บ (law/uploads.py) — ไม่ถูกล้างตอน seed ----------
-- processing → draft (ตรวจทาน/แก้ได้) → approved (เผยแพร่ — seed นำเข้าซ้ำจาก draft ทุกครั้ง) | rejected | failed
-- ผู้อนุมัติต้องไม่ใช่ผู้อัปโหลด; ไฟล์ต้นฉบับอยู่ใน raw_file (sha256)
CREATE TABLE IF NOT EXISTS law_upload (
    upload_id      SERIAL PRIMARY KEY,
    sha256         CHAR(64) NOT NULL,
    original_name  TEXT NOT NULL,
    ext            VARCHAR(8) NOT NULL,
    classification VARCHAR(10) NOT NULL CHECK (classification IN ('public', 'internal')),
    doc_type       VARCHAR(40) NOT NULL,
    register_id    INT,            -- ไม่มี FK: law_register สร้างใหม่ทุกครั้งที่ seed (ผูกซ้ำด้วย register_title)
    register_title TEXT,
    effective_date DATE,
    force_ocr      BOOLEAN NOT NULL DEFAULT FALSE,
    status         VARCHAR(12) NOT NULL DEFAULT 'processing',
    method         VARCHAR(8),     -- docx | doc | pdf | ocr
    draft          JSONB,          -- ตราสารรูปแบบเดียวกับ seed/laws/*.json
    error          TEXT,
    uploaded_by    VARCHAR(40) NOT NULL,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    edited_by      VARCHAR(40),
    edited_at      TIMESTAMPTZ,
    reviewed_by    VARCHAR(40),
    reviewed_at    TIMESTAMPTZ,
    review_note    TEXT
);

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

-- ---------- embedding index จากโมเดล (search/vector_index.py) — ไม่ถูกล้างตอน seed ----------
-- search_chunk.embedding (hash) ใช้เมื่อไม่มี index ที่ active; สร้าง index ใหม่ได้ทุกครั้งที่เปลี่ยนโมเดล แล้วสลับใช้เมื่อพร้อม
-- external = TRUE: สร้างเวกเตอร์เฉพาะตัวบทกฎหมายสาธารณะ (ข้อมูลภายในไม่ถูกส่งไปบริการภายนอก)
DO $$ BEGIN CREATE EXTENSION IF NOT EXISTS vector; EXCEPTION WHEN OTHERS THEN NULL; END $$;
CREATE TABLE IF NOT EXISTS embedding_index (
    index_id     SERIAL PRIMARY KEY,
    provider     VARCHAR(20) NOT NULL,
    base_url     VARCHAR(300) NOT NULL,
    model        VARCHAR(120) NOT NULL,
    dim          INT,
    external     BOOLEAN NOT NULL,
    status       VARCHAR(12) NOT NULL DEFAULT 'building',  -- building | ready | failed
    active       BOOLEAN NOT NULL DEFAULT FALSE,
    activate_when_ready BOOLEAN NOT NULL DEFAULT FALSE,
    total        INT NOT NULL DEFAULT 0,   -- เนื้อหาที่ต้องสร้างเวกเตอร์ (ไม่ซ้ำ)
    done         INT NOT NULL DEFAULT 0,
    withheld     INT NOT NULL DEFAULT 0,   -- ข้ามเพราะเป็นข้อมูลภายใน และโมเดลอยู่ภายนอก
    error        TEXT,
    created_by   VARCHAR(40) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS embedding_index_one_active ON embedding_index (active) WHERE active;
CREATE TABLE IF NOT EXISTS embedding_vec (
    index_id    INT NOT NULL REFERENCES embedding_index ON DELETE CASCADE,
    content_sha CHAR(64) NOT NULL,
    vec         REAL[] NOT NULL,
    PRIMARY KEY (index_id, content_sha)
);

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


-- ค่าตั้งระบบที่ผู้ดูแลแก้ผ่านหน้าเว็บ (ทับค่าจาก .env) — ไม่ถูกล้างตอน seed
-- secret = TRUE: value เข้ารหัสด้วย CONFIG_SECRET (Fernet) และไม่ส่งกลับไปหน้าเว็บ
CREATE TABLE IF NOT EXISTS app_setting (
    key         VARCHAR(60) PRIMARY KEY,
    value       TEXT NOT NULL,
    secret      BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by  VARCHAR(40) NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
