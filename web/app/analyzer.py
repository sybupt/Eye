"""
analyzer.py — 年龄判读、衍生指标计算与汇总分析
儿童近视防控的规则核心都在这里。
"""
import re
import json
from datetime import date

# 远视储备年龄参考下限（D，散瞳SE）—— 综合国家指南与专家共识
RESERVE_MIN = {
    (3, 5): 2.00, (6, 6): 1.00, (7, 7): 1.38, (8, 8): 1.00,
    (9, 9): 0.88, (10, 12): 0.50, (13, 15): 0.25,
}
# 视力年龄下限（小数）
VA_MIN = {(3, 3): 0.5, (4, 4): 0.5, (5, 5): 0.6, (6, 99): 0.7}

# ---- "以时间为主线"对比矩阵的展示配置（按类别聚合，同类指标放一起）----
MATRIX_GROUPS = [
    ("VA", "视力", [
        ("VA_UCVA", "裸眼视力", ""),
        ("VA_BCVA", "矫正视力", ""),
    ]),
    ("REF", "屈光 · 远视储备", [
        ("REF_SPH", "球镜 S", "D"),
        ("REF_CYL", "柱镜 C", "D"),
        ("REF_AXIS", "轴位", "°"),
        ("REF_SE", "等效球镜 SE", "D"),
        ("PED_HYPEROPIA_RESERVE", "远视储备", "D"),
        ("PED_SE_PROGRESS", "近视年进展", "D/年"),
        ("REF_PD", "瞳距 PD", "mm"),
    ]),
    ("BIO", "眼生物测量", [
        ("BIO_AL", "眼轴长度 AL", "mm"),
        ("PED_AL_GROWTH", "眼轴年增长", "mm/年"),
        ("BIO_K1", "角膜曲率 K1", "D"),
        ("BIO_K2", "角膜曲率 K2", "D"),
        ("BIO_AST", "角膜散光 ΔK", "D"),
        ("BIO_ALCR", "AL/CR 比", ""),
        ("BIO_ACD", "前房深度 ACD", "mm"),
        ("BIO_WTW", "白到白 WTW", "mm"),
    ]),
    ("IOP", "眼压", [("IOP_NCT", "眼压 NCT", "mmHg")]),
    ("OCT", "OCT / 眼底", [
        ("OCT_CMT", "黄斑中心厚度", "μm"),
        ("OCT_RNFL", "RNFL 厚度", "μm"),
        ("OCT_CD", "杯盘比 C/D", ""),
    ]),
    ("VF", "视野", [
        ("VF_MD", "视野 MD", "dB"),
        ("VF_PSD", "视野 PSD", "dB"),
    ]),
    ("BV", "双眼视功能", [
        ("BV_NRA", "NRA", "D"), ("BV_PRA", "PRA", "D"),
        ("BV_ACA", "AC/A", "△/D"), ("BV_BCC", "BCC", "D"),
        ("BV_NPC", "集合近点", "cm"), ("BV_STEREO", "立体视", "秒弧"),
    ]),
    ("DRY", "干眼 / 眼表", [
        ("DRY_BUT", "泪膜破裂时间", "秒"),
        ("DRY_SCHIRMER", "泪液分泌", "mm"),
        ("DRY_TMH", "泪河高度", "mm"),
    ]),
]

REF_TEXT = {
    "VA_UCVA": "6岁+ ≥0.7", "VA_BCVA": "≥1.0",
    "REF_SE": "负值为近视；儿童须散瞳判读",
    "PED_HYPEROPIA_RESERVE": "6岁参考下限约 +1.00D",
    "REF_PD": "双眼瞳孔中心距离，配镜必需",
    "PED_AL_GROWTH": "6-10岁 <0.20mm/年",
    "PED_SE_PROGRESS": "年化 >0.50D 需警惕",
    "BIO_AL": "6岁约22.5 → 成人23.5mm",
    "BIO_AST": "生理性散光 <1.50D",
    "BIO_ALCR": ">3.0 提示近视风险增加",
    "BIO_ACD": "约2.5~3.5mm，<2.5警惕闭角",
    "BIO_WTW": "约10.5~12.5mm",
    "IOP_NCT": "10~21 mmHg",
    "OCT_CMT": "约170~290μm（设备相关）",
    "DRY_BUT": "≥10秒",
}

SIMPLE_RANGE = {
    "IOP_NCT": (10, 21),
    "OCT_CMT": (170, 290),
    "DRY_BUT": (10, 60),
    "DRY_SCHIRMER": (10, 40),
}


def is_cyclo_note(note_text: str) -> bool:
    """判断备注是否表示'已散瞳'。必须排除'未散瞳/非散瞳'这类否定表述，
    否则 OCR 字段里的'未散瞳，不可作远视储备'会被误判为散瞳。"""
    if not note_text:
        return False
    # 去掉否定短语后再找"散瞳"
    cleaned = re.sub(r"未散瞳|非散瞳|没有散瞳|不散瞳", "", note_text)
    return "散瞳" in cleaned


def _cell_flag(code, v, age, cyclo):
    """单个数值的判读标记（用于对比矩阵单元格着色）"""
    if v is None:
        return ""
    if code in ("VA_UCVA", "VA_BCVA"):
        return va_flag(v, age)
    if code == "REF_SE":
        return reserve_flag(v, age, cyclo)[0] if cyclo else "UNKNOWN"
    rng = SIMPLE_RANGE.get(code)
    if rng:
        lo, hi = rng
        return "NORMAL" if lo <= v <= hi else ("LOW" if v < lo else "HIGH")
    return ""

# 眼轴年增长预警阈值（按年龄，mm/年）
AL_GROWTH_WARN = [(6, 0.30), (10, 0.20), (15, 0.15), (200, 0.10)]


def _age_bucket(age):
    if age is None:
        return None
    a = int(age)
    for (lo, hi), v in RESERVE_MIN.items():
        if lo <= a <= hi:
            return v
    return None


def va_flag(value, age):
    if value is None:
        return "UNKNOWN"
    a = int(age) if age is not None else 99
    for (lo, hi), minv in VA_MIN.items():
        if lo <= a <= hi:
            return "NORMAL" if value >= minv else "LOW"
    return "NORMAL" if value >= 1.0 else "LOW"


def reserve_flag(se, age, cycloplegic):
    """远视储备判读：只有在散瞳结果上才有意义"""
    if se is None:
        return "UNKNOWN", "无屈光度数据"
    if not cycloplegic:
        return "UNKNOWN", "未散瞳，SE 不可直接作远视储备"
    ref = _age_bucket(age)
    if ref is None:
        return "UNKNOWN", "缺年龄参考"
    if se < -0.50:
        return "CRITICAL", f"已达近视范围（SE {se:+.2f}D）"
    if se < ref:
        return "WARN", f"低于同龄参考下限 +{ref:.2f}D，近视前驱期"
    return "NORMAL", f"储备充足（参考下限 +{ref:.2f}D）"


def myopia_stage(se, al, age, cycloplegic, fundus_note=""):
    """近视分期：按指南口径，取较差眼。se/al 可传数值列表或单值。"""
    def _vals(x):
        if x is None:
            return []
        if isinstance(x, (int, float)):
            return [x]
        return [v for v in x if v is not None]

    se_vals = _vals(se)
    al_vals = _vals(al)
    se_val = min(se_vals) if se_vals else None       # 较差眼 = 球镜更负
    al_val = max(al_vals) if al_vals else None       # 较差眼 = 眼轴更长
    if fundus_note and any(k in fundus_note for k in ["葡萄肿", "萎缩", "漆裂纹", "新生血管", "劈裂"]):
        return "病理性近视期", "眼底出现特征性病变"
    if al_val is not None and al_val >= 26.0:
        return "高度近视期", f"眼轴 {al_val:.2f}mm ≥ 26.00mm"
    if se_val is None:
        return "待评估", "缺少屈光度数据"
    if se_val <= -6.0:
        return "高度近视期", f"SE {se_val:+.2f}D ≤ -6.00D"
    if se_val <= -3.0:
        return "中度近视", f"SE {se_val:+.2f}D"
    if se_val <= -0.5:
        return "低度近视", f"SE {se_val:+.2f}D"
    if se_val < 0.5 and age is not None and age <= 15:
        return "正视化完成", f"SE {se_val:+.2f}D"
    return "未近视", f"SE {se_val:+.2f}D"


def al_growth_warn(age, growth):
    if growth is None:
        return False, ""
    age_i = int(age) if age is not None else 10
    threshold = 0.30
    for upper, th in AL_GROWTH_WARN:
        if age_i < upper:
            threshold = th
            break
    return growth > threshold, f"阈值 {threshold:.2f}mm/年"


def _annualized(v1, v2, d1, d2):
    """计算年化变化量"""
    if v1 is None or v2 is None or not d1 or not d2:
        return None
    try:
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        days = (b - a).days
        if days <= 0:
            return None
        return round((v2 - v1) / (days / 365.25), 3)
    except Exception:
        return None


def compute_derived(conn, patient_id):
    """重算某患者全部衍生指标。返回写入条数。"""
    from .db import age_at, rows_to_dicts
    sessions = rows_to_dicts(conn.execute(
        "SELECT * FROM encounters WHERE patient_id=? ORDER BY exam_date", (patient_id,)
    ).fetchall())
    patient = dict(conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone() or {})
    if not patient:
        return 0
    birth = patient["birth_date"]
    obs = rows_to_dicts(conn.execute(
        "SELECT * FROM observations WHERE patient_id=? AND verify_status='VERIFIED' ORDER BY encounter_id",
        (patient_id,),
    ).fetchall())
    by_enc = {}
    for o in obs:
        by_enc.setdefault(o["encounter_id"], {})[(o["indicator_code"], o["side"])] = o
    # 散瞳信息从 documents.ocr_json 无法可靠回读，简化：若存在远视储备衍生则按散瞳处理由前端标记
    # 这里把每次检查的 cycloplegic 存在 encounter 备注字段中（复用 doctor_advice 前缀不便），
    # 改为从 observations.note 探测
    enc_cyclo = {}
    for eid, mp in by_enc.items():
        notes = " ".join((o["note"] or "") for o in mp.values())
        enc_cyclo[eid] = is_cyclo_note(notes)

    def put(eid, code, side, value, flag="", basis="", value_text=None):
        conn.execute(
            """INSERT INTO derived_metrics (patient_id,encounter_id,indicator_code,side,value_num,value_text,flag,basis)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(encounter_id,indicator_code,side) DO UPDATE SET
                 value_num=excluded.value_num, value_text=excluded.value_text,
                 flag=excluded.flag, basis=excluded.basis""",
            (patient_id, eid, code, side, value, value_text, flag, basis),
        )

    count = 0
    prev = None
    for e in sessions:
        eid = e["id"]
        mp = by_enc.get(eid, {})
        age = e["age_at_exam_y"] or age_at(birth, e["exam_date"])
        cyclo = enc_cyclo.get(eid, False)

        # 1) 远视储备 = 本次散瞳 SE（逐眼）；未散瞳不生成，避免误导
        if cyclo:
            for side in ("OD", "OS"):
                se_o = mp.get(("REF_SE", side))
                if se_o:
                    flag, reason = reserve_flag(se_o["value_num"], age, cyclo)
                    put(eid, "PED_HYPEROPIA_RESERVE", side, se_o["value_num"], flag, reason)
                    count += 1

        # 2) 眼轴年增长 & 3) 近视年进展（与上一次检查比）
        if prev is not None:
            prev_e, prev_mp = prev
            days = (date.fromisoformat(e["exam_date"]) - date.fromisoformat(prev_e["exam_date"])).days
            if 30 <= days <= 400:  # 只对比 1~13 个月内的相邻记录，避免跨度过大失真
                years = days / 365.25
                for side in ("OD", "OS"):
                    al1 = (prev_mp.get(("BIO_AL", side)) or {}).get("value_num")
                    al2 = (mp.get(("BIO_AL", side)) or {}).get("value_num")
                    if al1 and al2:
                        g = round((al2 - al1) / years, 3)
                        over, ths = al_growth_warn(age, g)
                        put(eid, "PED_AL_GROWTH", side, g, "WARN" if over else "NORMAL",
                            f"{prev_e['exam_date']}→{e['exam_date']} {ths}")
                        count += 1
                    se1 = (prev_mp.get(("REF_SE", side)) or {}).get("value_num")
                    se2 = (mp.get(("REF_SE", side)) or {}).get("value_num")
                    if se1 is not None and se2 is not None:
                        prog = round((se2 - se1) / years, 3)  # 负值=度数加深
                        put(eid, "PED_SE_PROGRESS", side, prog,
                            "WARN" if prog <= -0.5 else "NORMAL", "年化SE变化，负值代表近视加深")
                        count += 1
        prev = (e, mp)

        # 4) 近视分期（每次检查都给）；未散瞳时标注待确认
        ses = [mp[k].get("value_num") for k in mp if k[0] == "REF_SE"]
        als = [mp[k].get("value_num") for k in mp if k[0] == "BIO_AL"]
        stage, why = myopia_stage(ses, als, age, cyclo)
        if not cyclo and ses:
            why = "未散瞳，度数含调节因素，" + why + "（待散瞳验光确认）"
            stage = stage + "（待散瞳确认）"
        put(eid, "PED_MYopia_STAGE", "OU", None, stage, why, value_text=stage)
        count += 1

    conn.commit()
    return count


# 眼轴年龄参考中位数（mm，综合指南与人群研究，用于文字判读）
AL_REF_AGE = [(3, 21.0), (5, 22.0), (6, 22.5), (7, 23.0), (8, 23.2),
              (9, 23.5), (10, 23.6), (12, 24.0), (15, 23.4), (99, 23.5)]


def _al_ref(age):
    if age is None:
        return 23.5
    a = int(age)
    prev = 23.5
    for upper, v in AL_REF_AGE:
        if a <= upper:
            return v
        prev = v
    return prev


def build_assessment(patient, encs, obs_by_enc, der_by_enc):
    """生成文字化的「眼睛情况 + 防控建议」。健康管理口径，不构成诊断。"""
    if not encs:
        return None
    last = encs[-1]
    mp = obs_by_enc.get(last["id"], {})
    dm = der_by_enc.get(last["id"], {})
    age = last.get("age_at_exam_y")
    cyclo = any(is_cyclo_note(o.get("note") or "") for o in mp.values())

    def val(code, side):
        o = mp.get((code, side))
        return o.get("value_num") if o else None

    findings = []   # {level: ok|warn|danger|info, text}
    actions = []    # 防控建议

    # --- 眼轴 ---
    al = {s: val("BIO_AL", s) for s in ("OD", "OS")}
    al_vals = {s: v for s, v in al.items() if v is not None}
    if al_vals:
        ref = _al_ref(age)
        worse = max(al_vals.items(), key=lambda kv: kv[1])
        for s, v in al_vals.items():
            diff = v - ref
            lvl = "danger" if diff >= 0.8 else ("warn" if diff >= 0.3 else "ok")
            findings.append({
                "level": lvl,
                "text": f"{'右眼' if s=='OD' else '左眼'}眼轴 {v:.2f}mm"
                        f"（{age:.0f}岁参考约 {ref:.1f}mm，{'偏长 '+format(diff,'.2f')+'mm' if diff>=0.3 else '在正常范围'}）",
            })
        if len(al_vals) == 2:
            diff = abs(al["OD"] - al["OS"])
            if diff >= 0.3:
                findings.append({"level": "warn", "text": f"双眼眼轴相差 {diff:.2f}mm（≥0.30mm），提示双眼发育不均衡，需关注"})
        # 历史眼轴增长
        growths = []
        for e in reversed(encs):
            g = der_by_enc.get(e["id"], {}).get(("PED_AL_GROWTH", worse[0]))
            if g and g.get("value_num") is not None:
                growths.append((e["exam_date"], g["value_num"], g.get("flag", "")))
                break
        if growths:
            d, gv, gf = growths[0]
            lvl = "danger" if gf == "WARN" else "ok"
            findings.append({"level": lvl,
                             "text": f"眼轴年增长 {gv:+.2f}mm/年（截至 {d}）——{'超过该年龄预警阈值，属重点监测' if gf=='WARN' else '在生理性增长范围内'}"})

    # --- 屈光 / 远视储备 ---
    se = {s: val("REF_SE", s) for s in ("OD", "OS")}
    se_vals = {s: v for s, v in se.items() if v is not None}
    if se_vals:
        if cyclo:
            for s, v in se_vals.items():
                lvl = "danger" if v < -0.5 else ("warn" if v < 0.5 else "ok")
                findings.append({"level": lvl,
                                 "text": f"{'右眼' if s=='OD' else '左眼'}散瞳等效球镜 {v:+.2f}D——"
                                         f"{'已达近视范围' if v<-0.5 else ('远视储备偏低' if v<0.5 else '储备尚可')}"})
        else:
            se_txt = " / ".join(
                f"{'右眼OD' if s=='OD' else '左眼OS'} {v:+.2f}D" for s, v in sorted(se_vals.items())
            )
            findings.append({"level": "info",
                             "text": f"本次为显然/电脑验光（未散瞳），等效球镜 {se_txt}。"
                                     f"未散瞳结果含调节因素，不能据此判定真实远视储备或确诊近视。"})
            actions.append("尽快完成散瞳验光（睫状肌麻痹验光，如复方托吡卡胺/环喷托酯），这是判定远视储备与真假性近视的金标准")

    # --- AL/CR ---
    alcr = {s: val("BIO_ALCR", s) for s in ("OD", "OS")}
    alcr_vals = [v for v in alcr.values() if v is not None]
    if alcr_vals:
        m = max(alcr_vals)
        findings.append({"level": "warn" if m > 3.0 else "ok",
                         "text": f"AL/CR 比（眼轴/角膜曲率半径）最高 {m:.2f}——"
                                 f"{'>3.0 提示近视发生风险增加' if m>3.0 else '未见明显异常'}（需结合眼轴与曲率综合看）"})

    # --- 矫正视力 ---
    bcva = {s: val("VA_BCVA", s) for s in ("OD", "OS")}
    bcva_vals = [v for v in bcva.values() if v is not None]
    if bcva_vals:
        low = [f"{'右' if s=='OD' else '左'}眼 {v:.1f}" for s, v in bcva.items() if v is not None and v < 0.8]
        findings.append({"level": "warn" if low else "ok",
                         "text": ("矫正视力偏低：" + "、".join(low) + "，需排查弱视/屈光矫正不足") if low
                                 else f"双眼矫正视力 {'、'.join(f'{v:.1f}' for v in bcva_vals)}，达到同龄正常水平，暂无弱视证据"})

    # --- 角膜曲率 / 散光 ---
    k1 = {s: val("BIO_K1", s) for s in ("OD", "OS")}
    ast = {s: val("BIO_AST", s) for s in ("OD", "OS")}
    ast_vals = [v for v in ast.values() if v is not None]
    if ast_vals:
        hi = max(ast_vals)
        findings.append({"level": "warn" if hi > 1.5 else "ok",
                         "text": f"角膜散光最高 {hi:.2f}D——{'超过生理性范围，需结合视力评估' if hi>1.5 else '在正常生理范围内'}"})

    # --- 分期 ---
    stage_dm = dm.get(("PED_MYopia_STAGE", "OU"))
    stage = stage_dm.get("flag") if stage_dm and stage_dm.get("flag") else None

    # 建议（去重）
    if al_vals:
        actions.append("建立眼轴基线档案，建议每 3~6 个月复查眼轴+屈光度，观察年增长是否超过预警阈值")
    if not cyclo:
        if not any("散瞳验光" in a for a in actions):
            actions.append("本次未散瞳，远视储备结论待定，请补做散瞳验光后再评估储备消耗速度")
    if age is not None and age <= 15:
        actions.append("保证每天累计 2 小时以上户外自然光活动（最经济有效的近视防控措施）")
        actions.append("近距离用眼遵循 20-20-20 法则（每 20 分钟看 6 米外 20 秒），读写距离保持一尺，避免长时间连续近距离用眼")
    if any(f["level"] == "danger" or (f["level"] == "warn" and "眼轴" in f["text"]) for f in findings):
        actions.append("眼轴偏长/增长偏快，建议到儿童眼科/视光门诊评估是否需要角膜塑形镜（OK镜）、离焦镜片或低浓度阿托品等医学干预")
    actions.append("本分析为健康管理记录与人群参考判读，不能替代医生面诊；请携带完整既往记录供接诊医生综合判断")

    # 总体结论
    has_danger = any(f["level"] == "danger" for f in findings)
    has_warn = any(f["level"] == "warn" for f in findings)
    if not cyclo:
        headline = "本次检查提示眼轴偏长，但缺少散瞳验光，暂不能确定远视储备与真实屈光度——建议尽快补做散瞳验光后再下定论"
    elif has_danger:
        headline = "检查提示存在明确近视风险或已达近视范围，建议尽快到儿童眼科门诊评估干预方案"
    elif has_warn:
        headline = "部分指标处于预警区间，建议加强随访并改善用眼习惯"
    else:
        headline = "目前各项指标未见明显异常，建议保持良好用眼习惯并定期复查"

    return {
        "headline": headline,
        "stage": stage or "待评估（需散瞳验光确认）",
        "age": age, "cycloplegic": cyclo,
        "findings": findings,
        "actions": actions,
        "disclaimer": "以上基于人群参考区间与本次报告数据的健康管理提示，不构成诊断；具体请遵医嘱。",
    }


def build_summary(conn, patient_id):
    """生成汇总分析：时间线、最新值、趋势、儿童防控、预警、医生摘要"""
    from .db import rows_to_dicts
    patient = dict(conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone() or {})
    if not patient:
        return None
    encs = rows_to_dicts(conn.execute(
        """SELECT e.*, i.name AS institution FROM encounters e
           LEFT JOIN institutions i ON i.id=e.institution_id
           WHERE e.patient_id=? ORDER BY e.exam_date""", (patient_id,)
    ).fetchall())
    obss = rows_to_dicts(conn.execute(
        "SELECT * FROM observations WHERE patient_id=? AND verify_status='VERIFIED'", (patient_id,)
    ).fetchall())
    derived = rows_to_dicts(conn.execute(
        "SELECT * FROM derived_metrics WHERE patient_id=?", (patient_id,)
    ).fetchall())

    # 检查原值明细（TOPCON 小票等设备的原始多次测量）
    raw_reports = []
    for d in rows_to_dicts(conn.execute(
        "SELECT id,encounter_id,filename,doc_category,ocr_json FROM documents "
        "WHERE patient_id=? ORDER BY id", (patient_id,)
    ).fetchall()):
        try:
            meta = json.loads(d.get("ocr_json") or "{}")
        except (ValueError, TypeError):
            meta = {}
        rm = meta.get("raw_measurements") or {}
        if rm:
            raw_reports.append({
                "document_id": d["id"], "encounter_id": d["encounter_id"],
                "filename": d["filename"], "doc_category": d["doc_category"],
                "raw_measurements": rm,
            })

    obs_by_enc = {}
    for o in obss:
        obs_by_enc.setdefault(o["encounter_id"], {})[(o["indicator_code"], o["side"])] = o
    der_by_enc = {}
    for d in derived:
        der_by_enc.setdefault(d["encounter_id"], {})[(d["indicator_code"], d["side"])] = d

    # 时间线
    timeline = []
    for e in encs:
        mp = obs_by_enc.get(e["id"], {})
        timeline.append({
            "id": e["id"], "date": e["exam_date"], "age": e["age_at_exam_y"],
            "institution": e["institution"] or "", "visit_type": e["visit_type"],
            "indicator_count": len(mp),
            "diagnosis": e["diagnosis"],
        })

    # 核心指标最新值 + 趋势
    core_codes = ["VA_UCVA", "REF_SE", "BIO_AL", "BIO_K1", "IOP_NCT", "OCT_CMT"]
    latest = {}
    trends = []
    for code in core_codes:
        for side in ("OD", "OS"):
            series = [(e["exam_date"], obs_by_enc.get(e["id"], {}).get((code, side)))
                      for e in encs]
            series = [(d, o["value_num"]) for d, o in series if o and o.get("value_num") is not None]
            if not series:
                continue
            latest[f"{code}_{side}"] = {"value": series[-1][1], "date": series[-1][0], "unit": "D" if code.startswith("REF") else ("mm" if code == "BIO_AL" else "")}
            if len(series) >= 2:
                delta = round(series[-1][1] - series[0][1], 3)
                trends.append({"code": code, "side": side, "from": series[0][1], "to": series[-1][1],
                               "delta": delta, "first_date": series[0][0], "last_date": series[-1][0]})

    # 儿童防控
    last_enc = encs[-1] if encs else None
    ped = {}
    alerts = []
    if last_enc:
        mp = obs_by_enc.get(last_enc["id"], {})
        dm = der_by_enc.get(last_enc["id"], {})
        age = last_enc["age_at_exam_y"]
        cyclo = any(is_cyclo_note(o.get("note") or "") for o in mp.values())
        ses = [mp[k]["value_num"] for k in mp if k[0] == "REF_SE" and mp[k].get("value_num") is not None]
        als = [mp[k]["value_num"] for k in mp if k[0] == "BIO_AL" and mp[k].get("value_num") is not None]
        stage_dm = dm.get(("PED_MYopia_STAGE", "OU"))
        stage = stage_dm["flag"] if stage_dm and stage_dm["flag"] else myopia_stage(ses, als, age, cyclo)[0]
        reserves = []
        for side in ("OD", "OS"):
            d = dm.get(("PED_HYPEROPIA_RESERVE", side))
            if d:
                reserves.append({"side": side, "value": d["value_num"], "flag": d["flag"], "basis": d["basis"]})
                if d["flag"] in ("WARN", "CRITICAL"):
                    alerts.append({"level": "high", "text": f"{side} {d['basis']}"})
        growths = []
        for side in ("OD", "OS"):
            for enc in reversed(encs):
                g = der_by_enc.get(enc["id"], {}).get(("PED_AL_GROWTH", side))
                if g:
                    growths.append({"side": side, "value": g["value_num"], "flag": g["flag"],
                                    "date": enc["exam_date"], "basis": g["basis"]})
                    if g["flag"] == "WARN":
                        alerts.append({"level": "high",
                                       "text": f"{side} 眼轴年增 {g['value_num']:+.2f}mm，{g['basis']}"})
                    break
        for side in ("OD", "OS"):
            for enc in reversed(encs):
                p = der_by_enc.get(enc["id"], {}).get(("PED_SE_PROGRESS", side))
                if p:
                    if p["value_num"] is not None and p["value_num"] <= -0.5:
                        alerts.append({"level": "high",
                                       "text": f"{side} 近视年进展 {p['value_num']:+.2f}D/年，超过 50 度红线"})
                    break
        ped = {
            "age": age, "cycloplegic": cyclo, "stage": stage,
            "stage_basis": stage_dm["basis"] if stage_dm else "",
            "reserves": reserves, "growths": growths,
            "se_latest": ses, "al_latest": als,
        }

    # 趋势图数据（供 ECharts）
    series = {}
    for code in ["VA_UCVA", "REF_SE", "BIO_AL", "IOP_NCT", "OCT_CMT", "PED_HYPEROPIA_RESERVE"]:
        for side in ("OD", "OS"):
            pts = []
            source = obss if not code.startswith("PED_") else derived
            for e in encs:
                rec = (obs_by_enc if not code.startswith("PED_") else der_by_enc).get(e["id"], {}).get((code, side))
                if rec and rec.get("value_num") is not None:
                    pts.append({"date": e["exam_date"], "value": rec["value_num"]})
            if pts:
                series[f"{code}_{side}"] = pts

    # ---- 以时间为主线的指标对比矩阵：同指标聚合、同类分组、双眼并列 ----
    dates = [e["exam_date"] for e in encs]
    ages = [e["age_at_exam_y"] for e in encs]
    enc_cyclo = {}
    for e in encs:
        notes = " ".join((o.get("note") or "") for o in obs_by_enc.get(e["id"], {}).values())
        enc_cyclo[e["id"]] = is_cyclo_note(notes)

    def _lookup(src, enc_id, code, side):
        rec = src.get(enc_id, {}).get((code, side))
        return (rec.get("value_num"), rec.get("flag", "")) if rec else (None, "")

    OU_ONLY = {"REF_PD"}  # 瞳距只有一个双眼值，放在 OD 列展示，OS 列留空
    matrix_groups = []
    for gkey, gname, metrics in MATRIX_GROUPS:
        rows = []
        for code, name, unit in metrics:
            is_derived = code.startswith("PED_")
            src = der_by_enc if is_derived else obs_by_enc
            side_cells = {}
            has_data = False
            for side in ("OD", "OS"):
                cells = []
                for idx, e in enumerate(encs):
                    q_side = "OU" if code in OU_ONLY else side
                    v, dflag = _lookup(src, e["id"], code, q_side)
                    if code in OU_ONLY and side == "OS":
                        v, dflag = None, ""
                    flag = dflag if is_derived else _cell_flag(code, v, ages[idx], enc_cyclo[e["id"]])
                    cells.append({"v": v, "flag": flag})
                    if v is not None:
                        has_data = True
                vals = [c["v"] for c in cells if c["v"] is not None]
                delta = round(vals[-1] - vals[0], 3) if len(vals) >= 2 else None
                side_cells[side] = {"cells": cells, "delta": delta}
            if has_data:
                rows.append({"code": code, "name": name, "unit": unit,
                             "ref": REF_TEXT.get(code, ""), "sides": side_cells})
        if rows:
            matrix_groups.append({"key": gkey, "name": gname, "rows": rows})
    matrix = {
        "dates": dates, "ages": ages, "groups": matrix_groups,
        "cyclo": [bool(enc_cyclo[e["id"]]) for e in encs],
        "visit_types": [e["visit_type"] or "" for e in encs],
        "encounter_ids": [e["id"] for e in encs],
        "institutions": [e.get("institution") or "" for e in encs],
    }

    assessment = build_assessment(patient, encs, obs_by_enc, der_by_enc)

    return {
        "patient": patient, "encounters": timeline, "latest": latest, "trends": trends,
        "series": series, "pediatric": ped, "alerts": alerts, "matrix": matrix,
        "assessment": assessment, "raw_reports": raw_reports,
        "counts": {"encounters": len(encs), "observations": len(obss), "derived": len(derived)},
    }


# ===========================================================================
# 家长端「上传即出报告」：把一次上传的多份报告整理成看得懂的汇总
# 输入是 /api/scan 返回的 documents（含 fields / raw_measurements），不依赖入库。
# 口径：健康管理记录，不构成诊断；未散瞳 SE 不能当散瞳度数/配镜处方。
# ===========================================================================

# 指标展示元信息（名称/单位/参考/分类）
_PARENT_META = {
    "BIO_AL":    ("眼轴长度", "mm", "6岁约22.5，成人约23.5；增长过快需关注"),
    "REF_SE":    ("等效球镜", "D", "负值为近视；儿童以散瞳结果为准"),
    "REF_SPH":   ("球镜 S", "D", "负值为近视，正值为远视"),
    "REF_CYL":   ("柱镜 C", "D", "绝对值≥0.75 提示散光"),
    "REF_AXIS":  ("散光轴位", "°", "配合柱镜使用，1~180"),
    "REF_PD":    ("瞳距 PD", "mm", "两瞳孔中心距离，配镜必需"),
    "BIO_K1":    ("角膜曲率 K1", "D", "平坦子午线曲率"),
    "BIO_K2":    ("角膜曲率 K2", "D", "陡峭子午线曲率"),
    "BIO_AST":   ("角膜散光 ΔK", "D", "K2−K1，生理性一般 <1.50"),
    "BIO_ALCR":  ("AL/CR 比", "", ">3.0 提示近视风险增加"),
    "BIO_ACD":   ("前房深度 ACD", "mm", "约2.5~3.5mm"),
    "BIO_WTW":   ("白到白 WTW", "mm", "约10.5~12.5mm，隐形眼镜/ICL参考"),
    "VA_UCVA":   ("裸眼视力", "", "6岁+ ≥0.7"),
    "VA_BCVA":   ("矫正视力", "", "≥1.0，低于同龄需排查弱视"),
    "IOP_NCT":   ("眼压", "mmHg", "10~21"),
    "PED_HYPEROPIA_RESERVE": ("远视储备", "D", "仅散瞳SE可作远视储备"),
}

# 用户端重点展示的指标（顺序即展示顺序）
_PARENT_KEY_CODES = [
    "BIO_AL", "REF_SE", "REF_CYL", "REF_AXIS", "REF_SPH",
    "BIO_AST", "BIO_K1", "BIO_K2", "BIO_ALCR",
    "VA_UCVA", "VA_BCVA", "REF_PD", "BIO_ACD", "BIO_WTW", "IOP_NCT",
]


def _collect_parent_fields(docs):
    """把所有文档的字段聚合成 {(code, side): [值列表]}，并记录来源文档与散瞳信息。"""
    agg = {}
    sources = []
    all_warnings = []
    exam_dates = []
    any_cyclo = False
    for d in docs or []:
        name = d.get("filename") or "报告"
        if d.get("error"):
            all_warnings.append(f"{name}：{d['error']}")
            continue
        if d.get("cycloplegic"):
            any_cyclo = True
        if d.get("exam_date"):
            exam_dates.append(d["exam_date"])
        sources.append({
            "filename": name,
            "category": d.get("doc_category_name") or d.get("doc_category") or "",
            "exam_date": d.get("exam_date") or "",
            "cycloplegic": bool(d.get("cycloplegic")),
            "fields": len(d.get("fields", [])),
        })
        for f in d.get("fields", []):
            code, side = f.get("indicator_code"), f.get("side", "OU")
            v = f.get("value_num")
            if not code or v is None:
                continue
            note = f.get("note") or ""
            if is_cyclo_note(note):
                any_cyclo = True
            agg.setdefault((code, side), []).append({
                "v": float(v), "raw": f.get("raw_value", ""), "unit": f.get("unit", ""),
                "note": note, "source": name, "category": d.get("doc_category", ""),
                "cyclo": is_cyclo_note(note) or bool(d.get("cycloplegic")),
                "confidence": f.get("confidence", 1.0),
            })
        for w in (d.get("warnings") or []):
            all_warnings.append(f"{name}：{w}")
    return agg, sources, all_warnings, exam_dates, any_cyclo


def _pick(agg, code, side, prefer_cyclo=False):
    """取某个指标某只眼的代表值。

    来源优先级（同一指标出现在多份报告时）：
    - BIO_* 生物测量（眼轴/角膜曲率/角膜散光等）：IOLMaster 等专用生物测量仪(doc_category=BIO) 最可靠，
      优先于电脑验光小票里附带的角膜曲率。
    - 其余：按散瞳优先（可选）→ 置信度 → 顺序。
    """
    lst = agg.get((code, side), [])
    if not lst:
        return None
    key = lambda x: (
        1 if x.get("category") == "BIO" else 0,   # 生物测量优先专用设备
        1 if x.get("cyclo") else 0,                # 散瞳优先
        x.get("confidence", 1.0),
    )
    cand = sorted(lst, key=key)
    if prefer_cyclo:
        cyclo_only = [x for x in cand if x.get("cyclo")]
        if cyclo_only:
            cand = cyclo_only
    return cand[-1]


def _fmt(v):
    if v is None:
        return None
    if abs(v - round(v)) < 1e-9:
        return round(v)
    return round(v, 2)


def build_parent_report(docs, child=None, exam_date=None):
    """把一次上传的多份检查报告整理成家长可读汇总。

    docs: /api/scan 的 documents 列表
    child: {name, birth_date, sex}（可选，用于年龄判读）
    exam_date: 覆盖检查日期（可选）
    返回可直接渲染的结构化报告。
    """
    child = child or {}
    agg, sources, warnings, exam_dates, any_cyclo = _collect_parent_fields(docs)
    if not exam_date:
        exam_date = max(exam_dates) if exam_dates else ""

    # --- 年龄 ---
    age = child.get("age_y")
    if age is None and child.get("birth_date") and exam_date:
        try:
            b = date.fromisoformat(child["birth_date"])
            e = date.fromisoformat(exam_date)
            age = (e - b).days / 365.25
        except (ValueError, TypeError):
            age = None

    def val(code, side, prefer_cyclo=False):
        r = _pick(agg, code, side, prefer_cyclo=prefer_cyclo)
        return r["v"] if r else None

    def rec(code, side, prefer_cyclo=False):
        return _pick(agg, code, side, prefer_cyclo=prefer_cyclo)

    # ================= 近视与否 =================
    # 散瞳SE优先用于判读；没有散瞳则明确标注"未散瞳，不能确诊"
    se = {}
    se_rec = {}
    for s in ("OD", "OS"):
        r = rec("REF_SE", s, prefer_cyclo=True)
        if r:
            se[s] = r["v"]
            se_rec[s] = r
    se_vals = [v for v in se.values() if v is not None]

    if se_vals:
        worst = min(se_vals)
        if any_cyclo:
            stage, why = myopia_stage(se_vals, [val("BIO_AL", s) for s in ("OD", "OS") if val("BIO_AL", s)], age, True)
            if worst <= -0.50:
                myopia = {"label": "已达近视范围", "level": "high",
                          "text": f"散瞳验光等效球镜最低 {worst:+.2f}D（≤−0.50D），达到近视诊断标准。",
                          "stage": stage, "why": why}
            elif worst < 0:
                myopia = {"label": "临界 / 正视化", "level": "warn",
                          "text": f"散瞳等效球镜最低 {worst:+.2f}D，接近近视但尚未达诊断标准，属近视前期，需密切随访。",
                          "stage": stage, "why": why}
            else:
                myopia = {"label": "目前未近视", "level": "ok",
                          "text": f"散瞳等效球镜最低 {worst:+.2f}D，目前未见近视。",
                          "stage": stage, "why": why}
        else:
            # 未散瞳：不确诊，只给提示
            if worst <= -0.50:
                myopia = {"label": "提示近视可能（未散瞳）", "level": "warn",
                          "text": f"未散瞳电脑/显然验光等效球镜最低 {worst:+.2f}D。因含调节因素，不能据此确诊近视，请尽快补做散瞳验光确认。",
                          "stage": "待散瞳确认", "why": "未散瞳结果不可作为近视诊断依据"}
            elif worst < 0:
                myopia = {"label": "未见明显近视（未散瞳）", "level": "info",
                          "text": f"未散瞳等效球镜最低 {worst:+.2f}D，未见明显近视征象；仍建议散瞳验光明确远视储备。",
                          "stage": "待散瞳确认", "why": "未散瞳结果仅供参考"}
            else:
                myopia = {"label": "未见近视（未散瞳）", "level": "info",
                          "text": f"未散瞳等效球镜最低 {worst:+.2f}D。儿童远视储备须以散瞳验光为准。",
                          "stage": "待散瞳确认", "why": "未散瞳结果仅供参考"}
    else:
        myopia = {"label": "缺少屈光度", "level": "unknown",
                  "text": "本次报告未识别到屈光度（等效球镜），无法判断近视与否。",
                  "stage": "待评估", "why": "缺 REF_SE"}

    # ================= 散光与否 =================
    cyl_vals = [abs(val("REF_CYL", s)) for s in ("OD", "OS") if val("REF_CYL", s) is not None]
    ast_vals = [val("BIO_AST", s) for s in ("OD", "OS") if val("BIO_AST", s) is not None]
    ast_among = [abs(v) for v in ast_vals]
    sig = max(cyl_vals + ast_among) if (cyl_vals or ast_among) else None
    parts = []
    if cyl_vals:
        parts.append("屈光柱镜最高 " + f"{max(cyl_vals):.2f}D")
    if ast_vals:
        parts.append("角膜散光最高 " + f"{max(ast_among):.2f}D")
    if sig is None:
        astigmatism = {"label": "缺少散光数据", "level": "unknown",
                       "text": "本次报告未识别到柱镜或角膜散光，无法判断。"}
    elif sig < 0.75:
        astigmatism = {"label": "未见明显散光", "level": "ok",
                       "text": "、".join(parts) + "，处于生理性范围。"}
    elif sig < 1.50:
        astigmatism = {"label": "轻度散光", "level": "warn",
                       "text": "、".join(parts) + "，属轻度散光；若无视力下降可观察，具体以验光师判断为准。"}
    else:
        astigmatism = {"label": "散光较明显", "level": "high",
                       "text": "、".join(parts) + "，超过生理性范围，建议结合视力与验光结果由医生评估是否需矫正。"}

    # ================= 关键指标卡 =================
    key_metrics = []
    for code in _PARENT_KEY_CODES:
        od, os_ = val(code, "OD"), val(code, "OS")
        if od is None and os_ is None:
            continue
        name, unit, ref = _PARENT_META.get(code, (code, "", ""))
        flag = ""
        if code == "BIO_AL" and age is not None:
            r = _al_ref(age)
            worst_al = max([v for v in (od, os_) if v is not None], default=None)
            if worst_al is not None:
                diff = worst_al - r
                flag = "high" if diff >= 0.8 else ("warn" if diff >= 0.3 else "ok")
                ref = f"{age:.0f}岁参考约 {r:.1f}mm（当前偏长 {diff:.2f}mm）" if diff >= 0.3 else f"{age:.0f}岁参考约 {r:.1f}mm"
        elif code == "BIO_AST":
            worst_a = max([abs(v) for v in (od, os_) if v is not None], default=0)
            flag = "warn" if worst_a > 1.5 else "ok"
        elif code in ("REF_SE", "REF_SPH"):
            flag = "warn" if any(v is not None and v <= -0.5 for v in (od, os_)) and not any_cyclo else ("ok" if any_cyclo else "info")
        elif code == "VA_BCVA":
            worst_v = min([v for v in (od, os_) if v is not None], default=1.0)
            flag = "warn" if worst_v < 0.8 else "ok"
        elif code == "BIO_ALCR":
            worst_c = max([v for v in (od, os_) if v is not None], default=0)
            flag = "warn" if worst_c > 3.0 else "ok"
        key_metrics.append({
            "code": code, "name": name, "unit": unit, "ref": ref, "flag": flag,
            "od": _fmt(od), "os": _fmt(os_),
            "od_raw": rec(code, "OD")["raw"] if rec(code, "OD") else "",
            "os_raw": rec(code, "OS")["raw"] if rec(code, "OS") else "",
        })

    # ================= 配镜处方参考数据 =================
    rx_rows = []
    for code, label in (("REF_SPH", "球镜 S"), ("REF_CYL", "柱镜 C"),
                        ("REF_AXIS", "轴位 A"), ("VA_BCVA", "矫正视力")):
        od, os_ = val(code, "OD"), val(code, "OS")
        if od is None and os_ is None:
            continue
        _, unit, _ = _PARENT_META.get(code, (label, "", ""))
        rx_rows.append({"name": label, "unit": unit, "od": _fmt(od), "os": _fmt(os_)})
    pd = val("REF_PD", "OU")
    if pd is None:
        pd = val("REF_PD", "OD")

    prescription = {
        "rows": rx_rows,
        "pd": _fmt(pd),
        "pd_note": "来自电脑验光小票，配镜时需以验光师实测为准" if pd is not None else "本次报告未提供瞳距，配镜时需现场测量",
        "pupil_height": {"value": None, "note": "瞳高不在验光报告内，需配镜时按镜架现场测量"},
        "cyclo_rx": {
            "has_cyclo": any_cyclo,
            "se_od": _fmt(se.get("OD")), "se_os": _fmt(se.get("OS")),
            "sph_od": _fmt(val("REF_SPH", "OD", prefer_cyclo=True)),
            "sph_os": _fmt(val("REF_SPH", "OS", prefer_cyclo=True)),
            "note": ("本次含散瞳验光，散瞳屈光度可作远视储备判读依据" if any_cyclo
                     else "本次均为未散瞳检查——此处度数含调节因素，不能作为散瞳度数，也不能直接作为配镜处方；请补做散瞳验光"),
        },
        "disclaimer": "配镜处方须以医学验光（含散瞳主觉验光、试戴调整）为准；电脑验光与本报告数据仅供参考，不可直接用于配镜。",
    }

    # ================= 防控建议 =================
    advice = []
    if not any_cyclo:
        advice.append("尽快到正规医疗机构完成散瞳验光（睫状肌麻痹验光），这是判断真假近视与远视储备的金标准")
    if myopia.get("level") in ("high", "warn"):
        advice.append("建议到儿童眼科/视光门诊就诊，由医生结合散瞳验光、眼轴与眼底综合评估")
    if age is not None and age <= 15:
        advice.append("每天累计 2 小时以上户外自然光活动，是目前循证证据最充分的近视防控措施")
        advice.append("近距离用眼遵循 20-20-20 法则（每 20 分钟看 6 米外 20 秒），读写保持一尺距离")
    if any(m["code"] == "BIO_AL" and m["flag"] in ("warn", "high") for m in key_metrics):
        advice.append("眼轴偏长，建议建立随访档案，每 3~6 个月复查眼轴与屈光度，观察增长速度")
    if astigmatism.get("level") in ("warn", "high"):
        advice.append("散光较明显时，请结合裸眼/矫正视力由医生判断是否需要配镜矫正，避免长期视疲劳")
    advice.append("本汇总为健康管理记录，不能替代医生面诊；就诊时请携带原始报告")

    # ================= 总体结论 =================
    if not key_metrics:
        overall = {"label": "未识别到有效指标，请上传更清晰的报告照片", "level": "unknown"}
    elif myopia.get("level") == "high":
        overall = {"label": "建议尽快就诊评估", "level": "high"}
    elif not any_cyclo:
        overall = {"label": "建议补做散瞳验光后再下结论", "level": "warn"}
    elif myopia.get("level") == "warn":
        overall = {"label": "处于近视前期，需密切随访", "level": "warn"}
    else:
        overall = {"label": "目前未见明显异常，保持定期复查", "level": "ok"}

    return {
        "child": {"name": child.get("name", ""), "birth_date": child.get("birth_date", ""),
                  "sex": child.get("sex", ""), "age": round(age, 1) if age is not None else None},
        "exam_date": exam_date,
        "cycloplegic": any_cyclo,
        "verdict": {"myopia": myopia, "astigmatism": astigmatism, "overall": overall},
        "key_metrics": key_metrics,
        "prescription": prescription,
        "advice": advice,
        "sources": sources,
        "warnings": warnings,
        "disclaimer": "本报告由检查报告照片自动识别整理，属健康管理记录，不构成医疗诊断；所有数值请以纸质原件为准，最终结论请遵医嘱。",
        "raw_measurements": [{"filename": d.get("filename"), "raw": d.get("raw_measurements")}
                             for d in (docs or []) if d.get("raw_measurements")],
    }
