-- =============================================================
-- 个人医疗检查报告汇总系统 (PHR) — 眼科/视觉系统模块
-- 数据库结构定义 (PostgreSQL 14+)
-- 设计目标：多机构、多时间、多格式报告 → 以时间为轴的结构化指标库
-- 核心思想：原始文件永久留存 + 指标结构化 + 单位归一化 + 溯源可查 + 儿童纵向趋势
-- =============================================================

-- ---------- 0. 扩展 ----------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- 大小写不敏感文本

-- ---------- 1. 枚举类型 ----------
CREATE TYPE eye_side        AS ENUM ('OD', 'OS', 'OU');           -- 右/左/双眼
CREATE TYPE doc_type        AS ENUM ('PDF','JPG','JPEG','PNG','HEIC','TIFF','OTHER');
CREATE TYPE extraction_type AS ENUM ('MANUAL','PDF_TEXT','OCR','FHIR_IMPORT','DERIVED');
CREATE TYPE verify_status   AS ENUM ('PENDING','VERIFIED','REJECTED','NEED_REVIEW');
CREATE TYPE value_kind      AS ENUM ('NUMBER','ENUM','TEXT','IMAGE','BOOLEAN');

-- =============================================================
-- 2. 主体档案：支持「本人 + 子女」多成员档案（儿童防控核心）
-- =============================================================
CREATE TABLE patients (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id   uuid NOT NULL,                    -- 账号拥有者(监护人)
    display_name    text NOT NULL,                    -- 档案显示名(可脱敏，如"小明")
    relation        text DEFAULT 'SELF',              -- SELF/CHILD/SPOUSE/PARENT/OTHER
    birth_date      date,                             -- 用于自动计算检查时年龄 → 年龄别参考值
    sex             text CHECK (sex IN ('M','F','U')),
    id_masked       text,                             -- 脱敏证件号(仅存后4位等)
    created_at      timestamptz DEFAULT now(),
    updated_at      timestamptz DEFAULT now()
);
COMMENT ON TABLE patients IS '被检查人档案。儿童近视防控场景下，一个监护人可管理多个子女档案。';

-- ---------- 3. 检查机构字典 ----------
CREATE TABLE institutions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    aliases     text[],                               -- 同名机构的不同写法，用于报告归并
    level       text,                                 -- 三甲/专科/社区/学校筛查
    department  text,
    city        text,
    note        text,
    created_at  timestamptz DEFAULT now()
);
COMMENT ON TABLE institutions IS '不同机构报告的统一归集；aliases 用于把"XX眼科医院""XX市眼科医院"归一为同一机构。';

-- =============================================================
-- 4. 时间主线：就诊/检查事件（整个系统的骨架）
-- =============================================================
CREATE TABLE encounters (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    institution_id  uuid REFERENCES institutions(id),
    exam_date       date NOT NULL,                     -- 检查日期（时间轴排序主键）
    visit_type      text,                             -- 门诊/学校筛查/体检/复查/配镜
    age_at_exam_y   numeric(4,1),                      -- 检查时年龄(由birth_date计算，冗余存储便于查询)
    chief_complaint text,
    diagnosis       text,                             -- 本次结论(原文)
    doctor_advice   text,                             -- 医嘱(原文)
    follow_up_months int,                             -- 建议复查间隔(月)
    tags            text[],                           -- 近视筛查/散瞳/OK镜复查/高度近视随访...
    created_at      timestamptz DEFAULT now(),
    updated_at      timestamptz DEFAULT now()
);
CREATE INDEX idx_encounters_patient_date ON encounters(patient_id, exam_date DESC);
COMMENT ON TABLE encounters IS '一次就诊或一次检查事件。所有指标、文件、影像都挂接到具体 encounter，实现"以时间为主线"。';

-- =============================================================
-- 5. 原始文件：PDF / JPG / PNG 永久留存（可追溯、可回看原图）
-- =============================================================
CREATE TABLE documents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encounter_id    uuid REFERENCES encounters(id) ON DELETE SET NULL,
    institution_id  uuid REFERENCES institutions(id),
    file_type       doc_type NOT NULL,
    original_name   text,
    storage_key     text NOT NULL,                     -- 对象存储 key (S3/COS/OSS)
    file_size       bigint,
    sha256          text,                             -- 去重/完整性校验
    page_count      int DEFAULT 1,
    document_date   date,                             -- 报告上的日期(可能与就诊日不同)
    ocr_status      verify_status DEFAULT 'PENDING',
    ocr_engine      text,                             -- PaddleOCR / 腾讯云OCR / 大模型视觉抽取
    ocr_raw_json    jsonb,                            -- 原始抽取结果(含坐标/置信度)
    uploaded_at     timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX uq_documents_sha256 ON documents(patient_id, sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX idx_documents_encounter ON documents(encounter_id);
COMMENT ON TABLE documents IS '上传的原始报告文件。保留原件是医疗数据的底线：结构化结果可复核、可回溯、可重新抽取。';

CREATE TABLE document_pages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no         int NOT NULL,
    image_key       text,                             -- PDF转图或原图存储路径
    ocr_text        text,                             -- 该页OCR全文
    ocr_json        jsonb,                            -- 结构化抽取结果(带坐标与置信度)
    UNIQUE(document_id, page_no)
);

-- =============================================================
-- 6. 指标字典：指标主数据（对应 indicator_dictionary.json）
-- =============================================================
CREATE TABLE indicator_catalog (
    code            text PRIMARY KEY,                 -- 如 BIO_AL / REF_SE
    name            text NOT NULL,
    name_en         text,
    category        text NOT NULL,                    -- VA/REF/BIO/IOP/OCT/VF/BV/DRY/PED
    value_kind      value_kind NOT NULL,
    unit_canonical  text NOT NULL,                    -- 标准单位: D / mm / mmHg / μm / 秒 / dB ...
    side_applicable boolean DEFAULT true,
    pediatric_key   boolean DEFAULT false,            -- 是否儿童防控重点指标(前端优先展示)
    formula         text,                             -- 衍生/换算公式
    aliases         text[],                           -- OCR字段别名: 眼轴/AL/AL Length
    ref_config      jsonb,                            -- 参考范围配置(分段/公式/固定)
    source          text,
    sort_order      int DEFAULT 0
);
CREATE INDEX idx_indicator_category ON indicator_catalog(category, sort_order);
COMMENT ON TABLE indicator_catalog IS '指标字典。OCR抽取时靠 aliases 把不同机构的字段叫法映射到统一 code。';

-- =============================================================
-- 7. 核心大表：结构化指标观测值（一行 = 一次检查的一个指标的一只眼）
-- =============================================================
CREATE TABLE observations (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id          uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encounter_id        uuid NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    document_id         uuid REFERENCES documents(id) ON DELETE SET NULL,   -- 溯源到原件
    page_no             int,                            -- 溯源到页码
    indicator_code      text NOT NULL REFERENCES indicator_catalog(code),
    side                eye_side NOT NULL DEFAULT 'OU',

    value_num           numeric(12,4),                  -- 归一化后的标准值
    value_text          text,                           -- 枚举/文本型结果
    unit                text,                           -- 标准单位(与字典一致)
    raw_value           text,                           -- 报告原文值，如 "-1.75"/"4.9"/">10"
    raw_unit            text,                           -- 报告原文单位，如 "mmHg"/"D"

    -- 参考范围快照：检查当时按年龄/设备采用的参考区间，独立于字典版本演变
    ref_low             numeric(12,4),
    ref_high            numeric(12,4),
    ref_text            text,
    flag                text,                           -- NORMAL/LOW/HIGH/CRITICAL/UNKNOWN

    extraction_method   extraction_type DEFAULT 'MANUAL',
    extraction_confidence numeric(3,2),                 -- 0.00~1.00，OCR低置信度触发人工复核
    verify_status       verify_status DEFAULT 'PENDING',
    note                text,
    created_at          timestamptz DEFAULT now(),
    UNIQUE(encounter_id, indicator_code, side)          -- 同一次检查同一眼同一指标不重复
);
CREATE INDEX idx_obs_patient_indicator_time
    ON observations(patient_id, indicator_code, side, encounter_id);
CREATE INDEX idx_obs_encounter ON observations(encounter_id);
CREATE INDEX idx_obs_verify ON observations(verify_status) WHERE verify_status <> 'VERIFIED';
COMMENT ON TABLE observations IS '系统核心表。纵向趋势图/曲线全部由本表按 patient+indicator+side+时间 聚合而成。';

-- =============================================================
-- 8. 文字性结论 / 影像附件（不适合放入数值指标的报告内容）
-- =============================================================
CREATE TABLE findings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    encounter_id    uuid NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    document_id     uuid REFERENCES documents(id) ON DELETE SET NULL,
    category        text,                             -- 眼底描述/裂隙灯/诊断/医嘱
    side            eye_side DEFAULT 'OU',
    text            text NOT NULL,                    -- 原文描述
    structured      jsonb,                            -- 结构化标签(如 视网膜变性=是, C/D=0.4)
    created_at      timestamptz DEFAULT now()
);

CREATE TABLE media_assets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    encounter_id    uuid NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    document_id     uuid REFERENCES documents(id) ON DELETE SET NULL,
    kind            text,                             -- FUNDUS_PHOTO / OCT_IMAGE / SLIT_LAMP / ...
    side            eye_side,
    storage_key     text NOT NULL,                     -- 原图/裁剪图存储路径
    thumbnail_key   text,
    caption         text,
    created_at      timestamptz DEFAULT now()
);
COMMENT ON TABLE media_assets IS '眼底彩照、OCT图像等影像单独管理，就诊出示时可并排展示历次图像对比。';

-- =============================================================
-- 9. 衍生指标（儿童防控核心）：可物化，保证曲线与预警即时可用
-- =============================================================
CREATE TABLE derived_metrics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    encounter_id    uuid NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    indicator_code  text NOT NULL REFERENCES indicator_catalog(code),
    side            eye_side NOT NULL,
    value_num       numeric(12,4),
    value_text      text,
    ref_text        text,
    flag            text,                             -- WARNING/NORMAL/...
    basis           jsonb,                            -- 计算依据: 用了哪两次观测值、公式、参数
    computed_at     timestamptz DEFAULT now(),
    UNIQUE(encounter_id, indicator_code, side)
);
CREATE INDEX idx_derived_patient_metric ON derived_metrics(patient_id, indicator_code, side);
COMMENT ON TABLE derived_metrics IS '远视储备、眼轴年增长、近视年进展、近视分期等由原始观测值计算，单独存储便于预警与展示。';

-- =============================================================
-- 10. 就诊出示 / 分享给医生：受控访问链接
-- =============================================================
CREATE TABLE share_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    token_hash      text NOT NULL UNIQUE,              -- 分享令牌哈希，禁止明文存储
    scope           text DEFAULT 'ALL',               -- ALL / RECENT_1Y / PEDIATRIC_CORE
    pin_hash        text,                             -- 可选访问密码哈希
    expires_at      timestamptz NOT NULL,
    view_count      int DEFAULT 0,
    revoked         boolean DEFAULT false,
    created_at      timestamptz DEFAULT now()
);
COMMENT ON TABLE share_sessions IS '就诊现场出示用：生成一个限时只读链接/二维码，医生扫码即可查看该患者全部检查记录与趋势，无需安装 App，且可随时撤销。';

-- ---------- 11. 隐私审计 ----------
CREATE TABLE access_log (
    id              bigserial PRIMARY KEY,
    patient_id      uuid,
    share_session_id uuid,
    actor           text,                             -- OWNER / DOCTOR / SYSTEM
    action          text,                             -- VIEW / EXPORT / SHARE / OCR
    ip_masked       text,
    created_at      timestamptz DEFAULT now()
);

-- =============================================================
-- 12. 常用查询视图
-- =============================================================

-- 12.1 时间轴总览：某患者所有检查事件（前端首页时间线）
CREATE VIEW v_timeline AS
SELECT
    e.id            AS encounter_id,
    e.patient_id,
    e.exam_date,
    e.age_at_exam_y,
    i.name          AS institution,
    e.visit_type,
    e.diagnosis,
    (SELECT count(*) FROM documents d WHERE d.encounter_id = e.id)   AS doc_count,
    (SELECT count(*) FROM observations o WHERE o.encounter_id = e.id AND o.verify_status='VERIFIED') AS verified_indicators
FROM encounters e
LEFT JOIN institutions i ON i.id = e.institution_id;

-- 12.2 指标曲线数据：直接喂给前端 ECharts 的宽表
CREATE VIEW v_metric_series AS
SELECT
    o.patient_id,
    o.indicator_code,
    c.name          AS indicator_name,
    c.category,
    c.unit_canonical AS unit,
    o.side,
    e.exam_date,
    o.value_num,
    o.value_text,
    o.ref_low,
    o.ref_high,
    o.flag,
    o.document_id
FROM observations o
JOIN encounters e   ON e.id = o.encounter_id
JOIN indicator_catalog c ON c.code = o.indicator_code
WHERE o.verify_status = 'VERIFIED';

-- 12.3 儿童近视防控看板：核心趋势一屏可见
CREATE VIEW v_pediatric_dashboard AS
SELECT
    p.id AS patient_id,
    p.display_name,
    e.exam_date,
    e.age_at_exam_y,
    max(o.value_num) FILTER (WHERE o.indicator_code='VA_UCVA'   AND o.side='OD') AS va_od,
    max(o.value_num) FILTER (WHERE o.indicator_code='VA_UCVA'   AND o.side='OS') AS va_os,
    max(o.value_num) FILTER (WHERE o.indicator_code='REF_SE'    AND o.side='OD') AS se_od,
    max(o.value_num) FILTER (WHERE o.indicator_code='REF_SE'    AND o.side='OS') AS se_os,
    max(o.value_num) FILTER (WHERE o.indicator_code='BIO_AL'    AND o.side='OD') AS al_od,
    max(o.value_num) FILTER (WHERE o.indicator_code='BIO_AL'    AND o.side='OS') AS al_os,
    max(o.value_num) FILTER (WHERE o.indicator_code='PED_HYPEROPIA_RESERVE' AND o.side='OD') AS reserve_od,
    max(d.value_num) FILTER (WHERE d.indicator_code='PED_AL_GROWTH' AND d.side='OD') AS al_growth_od,
    max(d.value_text) FILTER (WHERE d.indicator_code='PED_MYopia_STAGE' AND d.side='OD') AS myopia_stage
FROM patients p
JOIN encounters e ON e.patient_id = p.id
LEFT JOIN observations o ON o.encounter_id = e.id
LEFT JOIN derived_metrics d ON d.encounter_id = e.id
GROUP BY p.id, p.display_name, e.id, e.exam_date, e.age_at_exam_y
ORDER BY p.id, e.exam_date;

-- =============================================================
-- 13. 年龄别参考值计算示例：按检查时年龄取远视储备下限
--     (生产环境建议把 ref_config 解析逻辑下沉到应用层)
-- =============================================================
-- SELECT code, name, ref_config FROM indicator_catalog WHERE pediatric_key;

-- =============================================================
-- 14. 数据入库典型流程（伪 SQL 顺序）
--   1) 上传文件 → INSERT documents (ocr_status=PENDING)
--   2) PDF转图 → INSERT document_pages；图片型直接 INSERT document_pages
--   3) OCR/视觉抽取 → UPDATE document_pages SET ocr_json；UPDATE documents SET ocr_status
--   4) 字段映射(aliases→code) → INSERT observations (verify_status=PENDING, 带置信度)
--   5) 低置信度/越界值 → 人工复核 → UPDATE observations SET verify_status='VERIFIED'
--   6) 计算衍生指标 → INSERT derived_metrics
--   7) 前端读 v_timeline / v_metric_series / v_pediatric_dashboard 渲染
-- =============================================================
