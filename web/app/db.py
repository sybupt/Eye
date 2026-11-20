"""
db.py — SQLite 运行时数据层
对应 schema/schema.sql 的 PostgreSQL 生产设计，本地原型用 SQLite 零配置运行。
"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
UPLOADS = DATA / "uploads"
PAGES = DATA / "pages"
DB_PATH = DATA / "phr_eye.db"
DICT_PATH = BASE / "schema" / "indicator_dictionary.json"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    relation TEXT DEFAULT 'SELF',
    birth_date TEXT,
    sex TEXT DEFAULT 'U',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS institutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    aliases TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    institution_id INTEGER,
    exam_date TEXT NOT NULL,
    visit_type TEXT DEFAULT '',
    age_at_exam_y REAL,
    diagnosis TEXT DEFAULT '',
    doctor_advice TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(patient_id, exam_date, institution_id)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    encounter_id INTEGER,
    institution_id INTEGER,
    filename TEXT,
    file_type TEXT,
    storage_path TEXT,
    sha256 TEXT,
    page_count INTEGER DEFAULT 1,
    doc_category TEXT DEFAULT '',
    ocr_text TEXT DEFAULT '',
    ocr_json TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS indicator_catalog (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT,
    value_kind TEXT,
    side_applicable INTEGER DEFAULT 1,
    pediatric_key INTEGER DEFAULT 0,
    formula TEXT,
    aliases TEXT DEFAULT '[]',
    ref_config TEXT DEFAULT '{}',
    source TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    encounter_id INTEGER NOT NULL,
    document_id INTEGER,
    indicator_code TEXT NOT NULL,
    side TEXT NOT NULL DEFAULT 'OU',
    value_num REAL,
    value_text TEXT,
    unit TEXT,
    raw_value TEXT,
    raw_unit TEXT,
    ref_text TEXT,
    flag TEXT DEFAULT 'UNKNOWN',
    extraction_method TEXT DEFAULT 'OCR',
    confidence REAL DEFAULT 1.0,
    verify_status TEXT DEFAULT 'VERIFIED',
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(encounter_id, indicator_code, side)
);

CREATE TABLE IF NOT EXISTS derived_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    encounter_id INTEGER NOT NULL,
    indicator_code TEXT NOT NULL,
    side TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    flag TEXT DEFAULT '',
    basis TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(encounter_id, indicator_code, side)
);

CREATE TABLE IF NOT EXISTS share_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    token TEXT NOT NULL UNIQUE,
    scope TEXT DEFAULT 'ALL',
    expires_at TEXT NOT NULL,
    revoked INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER,
    action TEXT,
    detail TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_enc_patient_date ON encounters(patient_id, exam_date);
CREATE INDEX IF NOT EXISTS idx_obs_patient_ind ON observations(patient_id, indicator_code, side);
CREATE INDEX IF NOT EXISTS idx_doc_patient ON documents(patient_id);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    for d in (DATA, UPLOADS, PAGES):
        d.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    # 载入指标字典
    with open(DICT_PATH, encoding="utf-8") as f:
        dic = json.load(f)
    n = 0
    for ind in dic["indicators"]:
        conn.execute(
            """INSERT INTO indicator_catalog
               (code,name,category,unit,value_kind,side_applicable,pediatric_key,formula,aliases,ref_config,source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, aliases=excluded.aliases, ref_config=excluded.ref_config,
                 pediatric_key=excluded.pediatric_key, unit=excluded.unit""",
            (
                ind["code"], ind["name"], ind["category"], ind.get("unit", ""),
                ind.get("value_type", "NUMBER"), 1 if ind.get("side") != "OU" else 0,
                1 if ind.get("pediatric_key") else 0, ind.get("formula", ""),
                json.dumps(ind.get("abbr", []), ensure_ascii=False),
                json.dumps(ind.get("reference", {}), ensure_ascii=False),
                ind.get("source", ""),
            ),
        )
        n += 1
    # 原型补充：K1/K2 分开存储，避免同一次检查同眼冲突
    for code, name in (("BIO_K1", "角膜曲率 K1（平K）"), ("BIO_K2", "角膜曲率 K2（陡K）")):
        conn.execute(
            """INSERT INTO indicator_catalog
               (code,name,category,unit,value_kind,side_applicable,pediatric_key,aliases,ref_config,source)
               VALUES (?,?,'BIO','D','NUMBER',1,1,'["K1","平K","K2","陡K"]','{"range":"40~47D"}','原型补充')
               ON CONFLICT(code) DO NOTHING""",
            (code, name),
        )
    conn.commit()
    conn.close()
    return n


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def age_at(birth_date, exam_date):
    """按检查日期计算年龄（岁，保留1位小数）"""
    if not birth_date or not exam_date:
        return None
    try:
        b = datetime.strptime(birth_date, "%Y-%m-%d").date()
        e = datetime.strptime(exam_date, "%Y-%m-%d").date()
        days = (e - b).days
        return round(max(days, 0) / 365.25, 1) if days >= 0 else None
    except Exception:
        return None


if __name__ == "__main__":
    print("init db:", DB_PATH, "indicators:", init_db())
