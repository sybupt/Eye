"""
main.py — FastAPI 应用入口
上传 → OCR → 字段抽取 → 人工确认 → 入库 → 汇总分析 → 就诊出示
"""
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import db, ocr_engine, extractor, analyzer

BASE = Path(__file__).resolve().parent
app = FastAPI(title="PHR 眼科检查报告汇总原型", version="1.0")
db.init_db()

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/files/uploads", StaticFiles(directory=db.UPLOADS), name="uploads")
app.mount("/files/pages", StaticFiles(directory=db.PAGES), name="pages")


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


# ---------- 字典 / 患者 ----------
@app.get("/api/dictionary")
def get_dictionary():
    import json as _json
    with open(db.DICT_PATH, encoding="utf-8") as f:
        return _json.load(f)


@app.get("/api/patients")
def list_patients():
    conn = db.get_conn()
    rows = db.rows_to_dicts(conn.execute("SELECT * FROM patients ORDER BY id").fetchall())
    conn.close()
    return rows


@app.post("/api/patients")
def create_patient(display_name: str = Form(...), birth_date: str = Form(""),
                   sex: str = Form("U"), relation: str = Form("SELF")):
    conn = db.get_conn()
    cur = conn.execute("INSERT INTO patients(display_name,birth_date,sex,relation) VALUES(?,?,?,?)",
                       (display_name, birth_date or None, sex, relation))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid}


# ---------- 上传 + OCR ----------
def _cyclo_override(value):
    """把前端显式传入的散瞳状态转成 bool；None 表示按报告文字自动判断。"""
    v = str(value or "").strip().lower()
    if v in ("true", "1", "yes", "on", "散瞳", "散瞳后", "已散瞳"):
        return True
    if v in ("false", "0", "no", "off", "未散瞳", "非散瞳"):
        return False
    return None


@app.post("/api/scan")
async def scan(files: list[UploadFile] = File(...), cycloplegic: str = Form("auto")):
    """上传一个或多个报告文件，返回 OCR 与字段抽取结果（不入库）"""
    cyc = _cyclo_override(cycloplegic)
    results = []
    for f in files:
        if not ocr_engine.is_allowed(f.filename or ""):
            results.append({"filename": f.filename, "error": "不支持的格式，请使用 PDF/JPG/PNG"})
            continue
        content = await f.read()
        if not content:
            results.append({"filename": f.filename, "error": "空文件"})
            continue
        rel, ext, sha, abs_path = ocr_engine.save_upload(content, f.filename)
        try:
            pages = ocr_engine.extract_pages(abs_path, ext, sha[:12])
        except Exception as e:
            results.append({"filename": f.filename, "error": f"OCR 失败: {e}"})
            continue
        try:
            extracted = extractor.extract_fields(pages, cyclo_override=cyc)
        except Exception as e:
            results.append({"filename": f.filename, "error": f"指标抽取失败: {type(e).__name__}: {e}"})
            continue
        results.append({
            "filename": f.filename, "file_type": ext, "storage_path": rel,
            "sha256": sha, "pages": len(pages),
            "page_confidence": [round(p["confidence"], 3) for p in pages],
            "page_images": [p["image_path"] for p in pages],
            **extracted,
        })
    return {"documents": results}


@app.post("/api/quick-add")
async def quick_add(
    files: list[UploadFile] = File(...),
    patient_id: int = Form(...),
    exam_date: str = Form(""),
    visit_type: str = Form("检查复查"),
    institution: str = Form(""),
    cycloplegic: str = Form("auto"),
):
    """简洁汇总页专用：上传报告→OCR→抽取→自动入库→返回更新后的完整汇总。
    无需人工复核步骤，识别到的有效指标全部入库；散瞳状态可由前端显式指定。"""
    cyc = _cyclo_override(cycloplegic)
    conn = db.get_conn()
    try:
        patient = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not patient:
            raise HTTPException(404, "患者不存在")

        docs = []
        for f in files:
            if not ocr_engine.is_allowed(f.filename or ""):
                docs.append({"filename": f.filename, "error": "不支持的格式"})
                continue
            content = await f.read()
            if not content:
                docs.append({"filename": f.filename, "error": "空文件"})
                continue
            rel, ext, sha, abs_path = ocr_engine.save_upload(content, f.filename)
            try:
                pages = ocr_engine.extract_pages(abs_path, ext, sha[:12])
                extracted = extractor.extract_fields(pages, cyclo_override=cyc)
            except Exception as e:
                docs.append({"filename": f.filename, "error": f"识别失败: {type(e).__name__}"})
                continue
            if cyc is not None:
                extracted["cycloplegic"] = cyc
            docs.append({
                "filename": f.filename, "file_type": ext, "storage_path": rel,
                "sha256": sha, "pages": len(pages), "doc_category": extracted.get("doc_category", ""),
                "page_images": [p["image_path"] for p in pages],
                "full_text": extracted.get("full_text", ""),
                "raw_measurements": extracted.get("raw_measurements", {}),
                "fields": extracted.get("fields", []),
                "exam_date": extracted.get("exam_date", ""),
                "institution": extracted.get("institution", ""),
                "cycloplegic": extracted.get("cycloplegic", False),
                "warnings": extracted.get("warnings", []),
            })

        ok_docs = [d for d in docs if not d.get("error") and d.get("fields")]
        # 检查日期：前端指定优先，否则取报告识别到的最早日期
        chosen_date = (exam_date or "").strip()
        if not chosen_date:
            dates = [d["exam_date"] for d in ok_docs if d.get("exam_date")]
            chosen_date = min(dates) if dates else datetime.now().date().isoformat()

        fields = []
        for di, d in enumerate(ok_docs):
            for f in d["fields"]:
                if f.get("value_num") is None:
                    continue
                fields.append({**f, "document_index": di})

        payload = {
            "patient_id": patient_id, "exam_date": chosen_date,
            "institution": (institution or "").strip() or (ok_docs[0].get("institution", "") if ok_docs else ""),
            "visit_type": visit_type, "diagnosis": "",
            "cycloplegic": (cyc is True) or (bool(ok_docs and ok_docs[0].get("cycloplegic")) if cyc is None else False),
            "documents": [{k: d.get(k) for k in ("filename", "file_type", "storage_path", "sha256",
                                                  "doc_category", "page_images", "full_text", "raw_measurements")}
                          for d in ok_docs],
            "fields": fields,
        }
        stats = _commit_payload(conn, payload)
        summary = analyzer.build_summary(conn, patient_id)
        conn.commit()
        return {
            "stats": stats,
            "recognitions": [{"filename": d["filename"], "category": d.get("doc_category", ""),
                              "date": d.get("exam_date", ""), "error": d.get("error"),
                              "warnings": d.get("warnings", []), "n_fields": len(d.get("fields", []))}
                             for d in docs],
            "chosen_date": chosen_date,
            "summary": summary,
        }
    finally:
        conn.close()


# ---------- 确认入库 ----------
def _commit_payload(conn, payload):
    """把一次识别结果写入档案库，返回统计。供 /api/commit 与 /api/quick-add 复用。"""
    pid = payload.get("patient_id")
    exam_date = payload.get("exam_date")
    if not pid or not exam_date:
        raise HTTPException(400, "缺少患者或检查日期")
    patient = conn.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    if not patient:
        raise HTTPException(404, "患者不存在")

    # 机构
    inst_name = (payload.get("institution") or "").strip()
    inst_id = None
    if inst_name:
        row = conn.execute("SELECT id FROM institutions WHERE name=?", (inst_name,)).fetchone()
        inst_id = row["id"] if row else conn.execute(
            "INSERT INTO institutions(name) VALUES(?)", (inst_name,)).lastrowid

    # 就诊事件（同患者+同日期+同机构 复用）
    row = conn.execute(
        "SELECT id FROM encounters WHERE patient_id=? AND exam_date=? AND COALESCE(institution_id,0)=COALESCE(?,0)",
        (pid, exam_date, inst_id),
    ).fetchone()
    if row:
        eid = row["id"]
        conn.execute(
            "UPDATE encounters SET visit_type=?, diagnosis=?, age_at_exam_y=? WHERE id=?",
            (payload.get("visit_type", ""), payload.get("diagnosis", ""),
             db.age_at(patient["birth_date"], exam_date), eid),
        )
    else:
        eid = conn.execute(
            """INSERT INTO encounters(patient_id,institution_id,exam_date,visit_type,diagnosis,age_at_exam_y)
               VALUES(?,?,?,?,?,?)""",
            (pid, inst_id, exam_date, payload.get("visit_type", ""), payload.get("diagnosis", ""),
             db.age_at(patient["birth_date"], exam_date)),
        ).lastrowid

    # 文件
    doc_ids = []
    for i, d in enumerate(payload.get("documents", [])):
        did = conn.execute(
            """INSERT INTO documents(patient_id,encounter_id,institution_id,filename,file_type,
                 storage_path,sha256,page_count,doc_category,ocr_text,ocr_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, eid, inst_id, d.get("filename", ""), d.get("file_type", ""),
             d.get("storage_path", ""), d.get("sha256", ""), d.get("page_count", 1),
             d.get("doc_category", ""), (d.get("full_text") or "")[:200],
             json.dumps({"page_images": d.get("page_images", []),
                         "raw_measurements": d.get("raw_measurements", {})}, ensure_ascii=False)),
        ).lastrowid
        doc_ids.append(did)

    # 指标
    written = 0
    skipped = 0
    cyclo = bool(payload.get("cycloplegic"))
    for f in payload.get("fields", []):
        code = f.get("indicator_code")
        side = f.get("side", "OU")
        v = f.get("value_num")
        if not code or v is None:
            skipped += 1
            continue
        note = f.get("note", "")
        if cyclo and code in ("REF_SE", "REF_SPH"):
            note = (note + "；散瞳验光").strip("；")
        try:
            conn.execute(
                """INSERT INTO observations(patient_id,encounter_id,document_id,indicator_code,side,
                     value_num,unit,raw_value,raw_unit,ref_text,flag,extraction_method,confidence,verify_status,note)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(encounter_id,indicator_code,side) DO UPDATE SET
                     value_num=excluded.value_num, raw_value=excluded.raw_value,
                     confidence=excluded.confidence, note=excluded.note, verify_status='VERIFIED'""",
                (pid, eid, doc_ids[min(int(f.get("document_index", 0)), max(len(doc_ids) - 1, 0))] if doc_ids else None,
                 code, side, float(v), f.get("unit", ""), str(f.get("raw_value", "")),
                 f.get("raw_unit", ""), "", "UNKNOWN", "OCR_REVIEWED",
                 float(f.get("confidence", 1.0)), "VERIFIED", note),
            )
            written += 1
        except Exception as e:
            skipped += 1
    conn.commit()
    derived_n = analyzer.compute_derived(conn, pid)
    conn.commit()
    return {"encounter_id": eid, "documents": len(doc_ids), "observations": written,
            "skipped": skipped, "derived_metrics": derived_n}


@app.post("/api/commit")
async def commit(payload: dict):
    """
    payload = {
      patient_id, exam_date, institution, visit_type, diagnosis, cycloplegic,
      documents: [ {filename,storage_path,sha256,file_type,doc_category,page_count,page_images} ],
      fields: [ {indicator_code,side,value_num,raw_value,unit,confidence,note,document_index} ]
    }
    """
    conn = db.get_conn()
    try:
        return _commit_payload(conn, payload)
    finally:
        conn.close()


# ---------- 手动补录 ----------
@app.post("/api/observations")
def add_observation(payload: dict):
    conn = db.get_conn()
    eid = payload.get("encounter_id")
    e = conn.execute("SELECT * FROM encounters WHERE id=?", (eid,)).fetchone()
    if not e:
        conn.close()
        raise HTTPException(404, "就诊记录不存在")
    conn.execute(
        """INSERT INTO observations(patient_id,encounter_id,indicator_code,side,value_num,unit,raw_value,
             extraction_method,verify_status,note)
           VALUES(?,?,?,?,?,?,?,'MANUAL','VERIFIED',?)
           ON CONFLICT(encounter_id,indicator_code,side) DO UPDATE SET
             value_num=excluded.value_num, raw_value=excluded.raw_value, note=excluded.note""",
        (e["patient_id"], eid, payload["indicator_code"], payload.get("side", "OU"),
         float(payload["value_num"]), payload.get("unit", ""), str(payload.get("raw_value", "")),
         payload.get("note", "手动补录")),
    )
    conn.commit()
    analyzer.compute_derived(conn, e["patient_id"])
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------- 汇总分析 ----------
@app.get("/api/summary/{patient_id}")
def summary(patient_id: int):
    conn = db.get_conn()
    result = analyzer.build_summary(conn, patient_id)
    conn.close()
    if not result:
        raise HTTPException(404, "患者不存在")
    return result


@app.get("/api/patient/{patient_id}/encounters")
def encounters(patient_id: int):
    conn = db.get_conn()
    rows = db.rows_to_dicts(conn.execute(
        "SELECT * FROM encounters WHERE patient_id=? ORDER BY exam_date DESC", (patient_id,)
    ).fetchall())
    conn.close()
    return rows


@app.get("/api/encounter/{eid}/observations")
def encounter_observations(eid: int):
    conn = db.get_conn()
    rows = db.rows_to_dicts(conn.execute(
        """SELECT o.*, c.name AS indicator_name FROM observations o
           LEFT JOIN indicator_catalog c ON c.code=o.indicator_code
           WHERE o.encounter_id=? ORDER BY o.indicator_code, o.side""", (eid,)
    ).fetchall())
    conn.close()
    return rows


@app.get("/api/documents/{patient_id}")
def documents(patient_id: int):
    conn = db.get_conn()
    rows = db.rows_to_dicts(conn.execute(
        """SELECT d.*, e.exam_date FROM documents d LEFT JOIN encounters e ON e.id=d.encounter_id
           WHERE d.patient_id=? ORDER BY d.id DESC""", (patient_id,)
    ).fetchall())
    conn.close()
    return rows


@app.get("/api/file/{doc_id}")
def doc_file(doc_id: int):
    conn = db.get_conn()
    d = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not d or not d["storage_path"]:
        raise HTTPException(404)
    p = db.UPLOADS / d["storage_path"]
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf" if d["file_type"] == "pdf" else None)


# ---------- 就诊出示 ----------
@app.post("/api/share")
def create_share(payload: dict):
    pid = payload.get("patient_id")
    hours = int(payload.get("hours", 24))
    token = secrets.token_urlsafe(12)
    expires = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    conn = db.get_conn()
    conn.execute("INSERT INTO share_sessions(patient_id,token,scope,expires_at) VALUES(?,?,?,?)",
                 (pid, token, payload.get("scope", "ALL"), expires))
    conn.execute("INSERT INTO access_log(patient_id,action,detail) VALUES(?,?,?)",
                 (pid, "SHARE", f"创建分享，{hours}小时"))
    conn.commit()
    conn.close()
    return {"token": token, "expires_at": expires}


@app.get("/share/{token}", response_class=HTMLResponse)
def share_view(token: str):
    conn = db.get_conn()
    s = conn.execute("SELECT * FROM share_sessions WHERE token=?", (token,)).fetchone()
    if not s or s["revoked"] or s["expires_at"] < datetime.now().isoformat(timespec="seconds"):
        conn.close()
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>链接已失效或已撤销</h2>", status_code=403)
    conn.execute("UPDATE share_sessions SET view_count=view_count+1 WHERE token=?", (token,))
    conn.execute("INSERT INTO access_log(patient_id,action,detail) VALUES(?,?,?)",
                 (s["patient_id"], "VIEW", "分享链接查看"))
    conn.commit()
    summary_data = analyzer.build_summary(conn, s["patient_id"])
    conn.close()
    # 只读视图：复用前端模板并注入只读数据
    html = (BASE / "static" / "share.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("/*__SHARE_DATA__*/",
                                     f"window.SHARE_DATA = {json.dumps(summary_data, ensure_ascii=False)};"))


@app.delete("/api/share/{token}")
def revoke_share(token: str):
    conn = db.get_conn()
    conn.execute("UPDATE share_sessions SET revoked=1 WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"status": "ok", "ocr": "tesseract chi_sim+eng", "db": str(db.DB_PATH)}


# ---------- 家长端：上传照片即出汇总报告（无需建档/入库） ----------
@app.get("/parent", response_class=HTMLResponse)
def parent_page():
    """家长端独立页面：只上传照片，直接得到看得懂的汇总报告。"""
    return (BASE / "static" / "parent.html").read_text(encoding="utf-8")


@app.post("/api/parent/analyze")
async def parent_analyze(
    files: list[UploadFile] = File(...),
    child_name: str = Form(""),
    birth_date: str = Form(""),
    sex: str = Form(""),
    exam_date: str = Form(""),
    cycloplegic: str = Form("auto"),
):
    """家长端一键分析：上传报告照片 → OCR → 抽取 → 生成家长可读汇总。
    不写入主档案库；结果为即时报告，可在页面打印/保存。"""
    cyc = _cyclo_override(cycloplegic)
    docs = []
    for f in files:
        if not ocr_engine.is_allowed(f.filename or ""):
            docs.append({"filename": f.filename, "error": "不支持的格式，请使用 JPG/PNG/PDF"})
            continue
        content = await f.read()
        if not content:
            docs.append({"filename": f.filename, "error": "空文件"})
            continue
        rel, ext, sha, abs_path = ocr_engine.save_upload(content, f.filename)
        try:
            pages = ocr_engine.extract_pages(abs_path, ext, sha[:12])
        except Exception as e:
            docs.append({"filename": f.filename, "error": f"图片识别失败: {e}"})
            continue
        try:
            extracted = extractor.extract_fields(pages, cyclo_override=cyc)
            if cyc is not None:
                extracted["cycloplegic"] = cyc
        except Exception as e:
            docs.append({"filename": f.filename, "error": f"指标抽取失败: {type(e).__name__}"})
            continue
        docs.append({
            "filename": f.filename, "file_type": ext, "storage_path": rel,
            "sha256": sha, "pages": len(pages),
            "page_images": [p["image_path"] for p in pages],
            **extracted,
        })
    child = {"name": child_name, "birth_date": birth_date, "sex": sex}
    report = analyzer.build_parent_report(docs, child=child, exam_date=exam_date or None)
    return report
