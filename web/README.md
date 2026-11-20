# PHR 眼科检查报告汇总系统（可运行原型）

一套真正能跑起来的本地应用：上传眼科检查报告 PDF 或照片 → OCR 自动识别 → 抽取结构化指标 → 人工复核确认 → 以时间为主线汇总分析 → 曲线图展示进展 → 就诊时一键出示给医生。

**当前聚焦儿童近视防控场景**，自动计算远视储备、眼轴年增速、近视年进展并给出分龄预警。

---

## 一、启动

环境要求：Python 3.10+，已安装 Tesseract OCR（含 `chi_sim` 中文包）。

```bash
cd phr-eye
python -m pip install fastapi uvicorn pillow pytesseract pymupdf python-multipart
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

浏览器打开：**http://127.0.0.1:8765/**

首次启动会自动建库（`data/phr_eye.db`）并载入指标字典，无需手动初始化。

## 二、使用流程

1. **建档**：首页选择/新建成员（含儿童，需填出生日期用于分龄判读）。
2. **上传报告**：选择 PDF / JPG / PNG，可一次多张；点「扫描识别」。
3. **复核抽取结果**：系统列出识别到的指标、数值、眼别、置信度；可直接修改，低置信度项建议核对原图。
4. **确认入库**：选定检查日期、机构、是否散瞳，提交。散瞳标记决定 SE 能否作为远视储备，务必准确。
5. **查看汇总**：时间轴列出每次检查；核心指标以曲线展示双眼进展；儿童防控看板给出分期、储备、增速与预警。
6. **就诊出示**：生成限时只读分享链接/二维码，可随时撤销，所有访问有审计记录。

## 三、已内置演示数据

`data/test_report1.png`（2025-06-15）与 `test_report2.png`（2026-05-20）为两份模拟散瞳验光+眼轴报告，展示从「远视储备充足」进展到「低度近视」的完整预警链路。端到端测试脚本：`data/e2e_test.py`。

## 四、目录结构

| 路径 | 说明 |
|---|---|
| `app/main.py` | FastAPI 入口，全部 API |
| `app/db.py` | SQLite 运行时数据层（对应 `schema/schema.sql` 的 PostgreSQL 生产设计） |
| `app/ocr_engine.py` | PDF/图片文字提取（Tesseract + PyMuPDF） |
| `app/extractor.py` | OCR 文本 → 结构化字段，含粘连修复、单位归一、别名映射 |
| `app/analyzer.py` | 年龄判读、衍生指标、儿童防控规则、汇总分析 |
| `app/static/` | 前端单页 + 就诊只读视图 + 本地 ECharts |
| `schema/indicator_dictionary.json` | 48 项指标主数据字典 |
| `schema/schema.sql` | PostgreSQL 生产建表脚本（12 表 3 视图） |
| `outputs/眼科检查报告梳理与结构化入库设计.html` | 设计与权威参考值文档 |

## 五、合规边界

所有参考值均为人群统计区间，受设备与个体差异影响，**仅供参考，以医生诊断为准**。本系统定位为健康管理记录工具，不做诊断输出。
