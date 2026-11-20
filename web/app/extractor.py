"""
extractor.py — 从 OCR 文本中抽取字段并映射到统一指标字典
规则驱动 + 置信度；不确定的值标 NEED_REVIEW，由用户在界面上确认/修正。
"""
import re
from datetime import date

NUM = r"([-+]?\d+(?:\.\d+)?)"

# 关键词字典：code -> (关键词列表, 单位, 最小值, 最大值, 是否默认需要眼别)
RULES = {
    "BIO_AL":      (["AL", "Axial Length", "眼轴", "眼轴长度", "眼轴长"], "mm", 15, 35, True),
    "BIO_K1":      (["K1", "平K", "Flat K", "Kf"], "D", 35, 50, True),
    "BIO_K2":      (["K2", "陡K", "Steep K", "Ks"], "D", 35, 50, True),
    "IOP_NCT":     (["NCT", "非接触眼压", "眼压"], "mmHg", 5, 40, True),
    "IOP_CCT":     (["CCT", "中央角膜厚度", "角膜厚度"], "μm", 400, 700, True),
    "OCT_CMT":     (["CMT", "CST", "中心凹厚度", "黄斑中心", "中心子区"], "μm", 100, 600, True),
    "OCT_RNFL":    (["RNFL", "神经纤维层"], "μm", 50, 180, True),
    "OCT_CD":      (["C/D", "杯盘比"], "", 0, 1.5, True),
    "VF_MD":       (["MD", "平均缺损"], "dB", -30, 10, True),
    "VF_PSD":      (["PSD", "模式标准差"], "dB", 0, 15, True),
    "BV_NRA":      (["NRA", "负相对调节"], "D", -1, 5, False),
    "BV_PRA":      (["PRA", "正相对调节"], "D", -6, 1, False),
    "BV_BCC":      (["BCC", "调节滞后", "调节超前"], "D", -1, 2, False),
    "BV_ACA":      (["AC/A", "ACA"], "△/D", 0, 15, False),
    "BV_NPC":      (["NPC", "集合近点", "辐辏近点"], "cm", 2, 30, False),
    "BV_STEREO":   (["立体视", "Stereo"], "秒弧", 10, 200, False),
    "DRY_BUT":     (["BUT", "泪膜破裂时间", "泪膜破裂"], "秒", 1, 60, True),
    "DRY_SCHIRMER":(["Schirmer", "泪液分泌"], "mm", 0, 40, True),
    "DRY_TMH":     (["TMH", "泪河高度"], "mm", 0.05, 2, True),
}

# 报告类型分类关键词（按权重）
CATEGORY_KEYWORDS = {
    "REF":  ["验光", "球镜", "柱镜", "轴位", "SPH", "CYL", "AXIS", "处方", "远视", "近视度数", "瞳距"],
    "BIO":  ["眼轴", "IOL Master", "Lenstar", "角膜曲率", "前房深度", "生物测量", "AL", "K1", "K2"],
    "VA":   ["视力表", "裸眼视力", "矫正视力", "视力检查", "视力筛查"],
    "IOP":  ["眼压", "IOP", "NCT", "Goldmann", "青光眼"],
    "OCT":  ["OCT", "黄斑", "视网膜神经纤维", "RNFL", "杯盘比", "眼底照相"],
    "VF":   ["视野", "Humphrey", "平均缺损", "MD", "PSD"],
    "BV":   ["视功能", "调节", "NRA", "PRA", "AC/A", "集合近点", "隐斜", "立体视"],
    "DRY":  ["干眼", "泪膜", "泪液分泌", "Schirmer", "BUT", "睑板腺"],
}
CATEGORY_NAMES = {
    "REF": "屈光/验光", "BIO": "眼生物测量", "VA": "视力检查", "IOP": "眼压/青光眼",
    "OCT": "OCT/眼底", "VF": "视野", "BV": "双眼视功能", "DRY": "干眼/眼表", "UNK": "未识别",
}

DATE_RE = re.compile(r"(20\d{2})\s*[-/\.年]\s*(\d{1,2})\s*[-/\.月]\s*(\d{1,2})")
SIDE_OD = re.compile(r"(?<![A-Z])(OD|R)(?![A-Z])|右眼?", re.I)
SIDE_OS = re.compile(r"(?<![A-Z])(OS|L)(?![A-Z])|左眼?", re.I)


def normalize_ocr(line: str) -> str:
    """修复 OCR 常见的字符粘连（不改原始文本，仅用于解析）"""
    s = line
    # 眼别字母与球镜标签粘连: "RS +0.25" / "LS-0.25" -> "R S +0.25" / "L S -0.25"
    s = re.sub(r"(?<![A-Za-z])([RL])([Ss])(?=\s*[-+]?\d)", r"\1 \2", s)
    # 大写眼别紧贴前一个大写字母: "IOPNCTOD 18" -> "IOPNCT OD 18"
    s = re.sub(r"([A-Z])(OD|OS)(?=\s|$)", r"\1 \2", s)
    # 缩写粘连: "IOPNCT" -> "IOP NCT"
    s = re.sub(r"(IOP)(NCT)", r"\1 \2", s)
    return s


def _side_of(line: str, default="OU"):
    """从一行文字判断眼别"""
    od = bool(SIDE_OD.search(line))
    os_ = bool(SIDE_OS.search(line))
    if od and not os_:
        return "OD"
    if os_ and not od:
        return "OS"
    return default


def _find_value(text: str, keywords, lo, hi):
    """在文本中查找 '关键词 数值' 模式，返回 (值, 原文片段, 匹配到的关键词)
    单字母关键词（S/C/A/K）要求字母边界，避免把 OS 里的 S 误当球镜。"""
    for kw in keywords:
        if len(kw) == 1 and kw.isalpha():
            pat = r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])\s*[:：=为]?\s*" + NUM
        else:
            pat = re.escape(kw) + r"\s*[:：=为]?\s*" + NUM
        m = re.search(pat, text, re.I)
        if m:
            v = float(m.group(1))
            if lo <= v <= hi:
                return v, m.group(0), kw
    return None, None, None


def detect_date(text: str):
    m = DATE_RE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            pass
    return None


def detect_cycloplegic(text: str):
    """是否散瞳验光 —— 决定 SE 能否当远视储备"""
    strong = ["睫状肌麻痹", "散瞳验光", "阿托品", "环喷托酯", "复方托吡卡胺", "散瞳后"]
    weak = ["散瞳"]
    if any(k in text for k in strong):
        return True
    if any(k in text for k in weak):
        return True
    return False


def classify(text: str):
    scores = {}
    for cat, kws in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(text.lower().count(k.lower()) for k in kws)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "UNK", scores
    return best, scores


def detect_institution(text: str):
    """粗提取机构名：含'医院/眼科/视光/门诊/中心'的短行"""
    for line in text.splitlines():
        line = line.strip()
        if 4 <= len(line) <= 40 and re.search(r"医院|眼科|视光|门诊|中心|保健院", line):
            return line
    return ""


# 这些关键词出现时，该行不是纯验光数据行（避免把眼轴/OCT行误解析为球镜柱镜）
NON_REF_HINTS = ["AL", "K1", "K2", "眼轴", "角膜曲率", "IOP", "NCT", "眼压",
                 "RNFL", "OCT", "CMT", "黄斑", "BUT", "Schirmer", "CCT", "泪膜",
                 "MD", "PSD", "NRA", "PRA", "AC/A", "集合近点", "立体视"]


def _parse_refraction_line(line, side_hint):
    """解析 'R -1.75 -0.50 180' 或 'S:-1.75 C:-0.50 A:180' 形态"""
    out = []
    # 去掉日期干扰
    clean = DATE_RE.sub(" ", line)
    # 若本行明确是视力行或其他检查行，跳过
    if re.search(r"视力|Vision|VA", clean, re.I):
        return out
    if any(h.lower() in clean.lower() for h in NON_REF_HINTS):
        return out
    # OCR 常见误识：$1.25 → S1.25（仅在该行有验光上下文时替换）
    clean = re.sub(r"\$\s*([+-]?\d)", r"S \1", clean)
    side = _side_of(clean, side_hint)
    # 先识别报告直接给出的 SE（如 "SE OD +1.00" / "SEOD +1.00"）
    for m in re.finditer(r"SE\s*(OD|OS)?\s*([-+]?\d+(?:\.\d+)?)", clean, re.I):
        se_side = _side_of(m.group(1), None) if m.group(1) else side
        se_side = se_side or side or "OU"
        out.append({"indicator_code": "REF_SE", "raw_value": m.group(2),
                    "value_num": float(m.group(2)), "unit": "D", "side": se_side,
                    "note": "报告直接给出的等效球镜"})
    # 带标签形态（用更严谨的标签列表，S/C/A 已加字母边界）
    sph, raw_s, _ = _find_value(clean, ["SPH", "S", "球镜"], -25, 25)
    cyl, raw_c, _ = _find_value(clean, ["CYL", "C", "柱镜", "散光"], -10, 10)
    axis, raw_a, _ = _find_value(clean, ["AXIS", "A", "轴位"], 0, 180)
    if sph is not None or cyl is not None:
        if sph is not None:
            out.append({"indicator_code": "REF_SPH", "raw_value": raw_s, "value_num": sph, "unit": "D", "side": side})
        if cyl is not None:
            out.append({"indicator_code": "REF_CYL", "raw_value": raw_c, "value_num": cyl, "unit": "D", "side": side})
        if axis is not None:
            out.append({"indicator_code": "REF_AXIS", "raw_value": raw_a, "value_num": axis, "unit": "°", "side": side})
        return out
    # 无标签形态：眼别 + 2~3 个数字（排除已由 SE 分支处理的行）
    nums = [float(x) for x in re.findall(NUM, clean)]
    if re.search(r"\bSE\b", clean, re.I):
        return out
    if len(nums) >= 2 and side != "OU":
        sph, cyl = nums[0], nums[1]
        if -25 <= sph <= 25 and -10 <= cyl <= 10:
            out.append({"indicator_code": "REF_SPH", "raw_value": str(sph), "value_num": sph, "unit": "D", "side": side})
            out.append({"indicator_code": "REF_CYL", "raw_value": str(cyl), "value_num": cyl, "unit": "D", "side": side})
            if len(nums) >= 3 and 0 <= nums[2] <= 180:
                out.append({"indicator_code": "REF_AXIS", "raw_value": str(nums[2]), "value_num": nums[2], "unit": "°", "side": side})
    return out


def _parse_va_line(line):
    """视力：支持小数记录与5分记录；一行内 OD/OS 分别提取"""
    if not re.search(r"视力|Vision|VA|UCVA|BCVA", line, re.I):
        return []
    out = []
    is_bcva = bool(re.search(r"矫正|戴镜|BCVA", line, re.I))
    # 逐眼提取：眼别标记后紧跟的可选标签 + 数字
    eye_pattern = r"(OD|OS|右眼?|左眼?)\s*(?:UCVA|BCVA|裸眼视力|矫正视力|视力)?\s*[:：]?\s*([-+]?\d+(?:\.\d+)?)"
    matches = list(re.finditer(eye_pattern, line, re.I))
    for m in matches:
        side = "OD" if re.search(r"OD|右", m.group(1), re.I) else "OS"
        raw = m.group(2)
        v = float(raw)
        if 4.0 <= v <= 5.3:
            val = round(10 ** (v - 5), 2)
            out.append({"indicator_code": "VA_BCVA" if is_bcva else "VA_UCVA",
                        "raw_value": raw, "value_num": val, "unit": "decimal", "side": side,
                        "note": f"5分记录 {v} 换算"})
        elif 0.1 <= v <= 2.0:
            out.append({"indicator_code": "VA_BCVA" if is_bcva else "VA_UCVA",
                        "raw_value": raw, "value_num": v, "unit": "decimal", "side": side})
    return out


# ---------------------------------------------------------------------------
# TOPCON 电脑验光/角膜曲率打印小票专用解析器
# 等宽字体、列对齐；含 REF.DATA(屈光) 与 KRT.DATA(角膜曲率) 两段。
# 电脑验光未散瞳，其 S.E. 不可直接当远视储备。
# ---------------------------------------------------------------------------

def is_topcon_receipt(text: str) -> bool:
    t = text.upper()
    return ("TOPCON" in t and ("REF." in t or "REF " in t) and
            ("KRT" in t or "PD:" in t.upper()))


def _num(s):
    try:
        return float(str(s).replace(",", ".").replace("−", "-"))
    except (ValueError, TypeError):
        return None


def _parse_topcon_receipt(page, warnings, raw_out=None):
    """解析 TOPCON 电脑验光小票。

    不依赖硬编码的绝对像素列位置：先按词聚类成行，再在每段内按数值的 x
    坐标动态分成 S/C/A（或 D/MM/A）三列，因此对分辨率、缩放、拍摄距离
    不同的同类小票都能适配。负号用"就近同列"规则判定（小票常把负号拆到
    相邻行）。同时保留每次测量的原始值到 warnings 之外的 raw_measurements
    字段（通过字段 note 传递），便于汇总时展示原数值。
    """
    words = page.get("words") or []
    if not words:
        return []
    lines = {}
    for w in words:
        y = w["bbox"][1]
        key = next((k for k in lines if abs(k - y) <= 9), y)
        lines.setdefault(key, []).append(w)
    rows = [(y, sorted(lines[y], key=lambda w: w["bbox"][0])) for y in sorted(lines)]

    def clean(t):
        return t.replace("\u2212", "-").replace("\uff0d", "-").replace("=", "-").replace("\u2014", "-")

    def rowtext(ws):
        return " ".join(clean(w["text"]) for w in ws)

    def find_y(pat, lo=0):
        for y, ws in rows:
            if y > lo and re.search(pat, rowtext(ws), re.I):
                return y
        return None

    y_ref = find_y(r"\bREF\b")
    y_krt = find_y(r"\bKRT\b", y_ref or 0)
    y_pd = find_y(r"\bPD\b", y_ref or 0)
    out = []
    if y_ref is None:
        return out

    ref_end = y_krt or y_pd or 10**9
    ref_rows = [(y, ws) for y, ws in rows if y_ref < y < ref_end]

    # 眼别分界：第一个含数值的 S.E. 行（每眼屈光段的末尾）
    y_split = None
    for y, ws in ref_rows:
        t = rowtext(ws)
        if re.search(r"S\.?\s*E\.?|SHE|\bSE\b", t, re.I) and re.search(r"\d", t):
            y_split = y
            break
    if y_split is None:
        y_l = find_y(r"<\s*L\s*>", y_ref)
        y_split = y_l if (y_l and y_ref < y_l < ref_end) else int((y_ref + ref_end) / 2)

    def split_columns(vals):
        """把 [(x,y,v)] 按 x 间隙分成若干簇，返回 (簇列表, 各簇的 x 中心)。"""
        if not vals:
            return [], []
        xs = sorted(v[0] for v in vals)
        if len(xs) < 2:
            return [list(vals)], [xs[0]]
        gaps = sorted(((xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)), reverse=True)
        gap, idx = gaps[0]
        # 只有当最大间隙足够明显时才切分，否则视为同一列
        if gap < 18:
            return [list(vals)], [sum(xs) / len(xs)]
        split_x = (xs[idx] + xs[idx + 1]) / 2
        left = [v for v in vals if v[0] < split_x]
        right = [v for v in vals if v[0] >= split_x]
        return [left, right], [
            sum(v[0] for v in left) / len(left) if left else 0,
            sum(v[0] for v in right) / len(right) if right else 0,
        ]

    def is_neg(sx, sy, signs):
        for gx, gy in signs:
            if abs(gx - sx) <= 75 and -12 <= gy - sy <= 22:
                return True
        return False

    # 全段所有孤立负号（用于 S.E. 就近判负，不受眼别分段影响）
    all_signs = []
    for y, ws in ref_rows:
        for w in ws:
            if clean(w["text"]).strip() == "-":
                all_signs.append((w["bbox"][0], y))

    # S.E. 直接读仪器计算值：按 S.E. 行出现顺序对应 OD、OS
    # （数值被 OCR 毁掉如 "4200" 时记为 None，回退用 S+C）
    se_rows = []
    for y, ws in ref_rows:
        t = rowtext(ws)
        if re.search(r"S\.?\s*E\.?|SHE|\bSE\b", t, re.I):
            vals = [(w["bbox"][0], y, float(clean(w["text"]).strip().replace(":", ".").replace(",", ".")))
                    for w in ws if re.fullmatch(r"\d{1,2}[.:]\d{2}", clean(w["text"]).strip())]
            if vals:
                vx, vy, vv = vals[0]
                se_rows.append(-vv if is_neg(vx, vy, all_signs) else vv)
            else:
                se_rows.append(None)
    se_by_side = {"OD": se_rows[0] if len(se_rows) > 0 else None,
                  "OS": se_rows[1] if len(se_rows) > 1 else None}

    for side, y0, y1 in (("OD", y_ref, y_split), ("OS", y_split, ref_end)):
        seg = [(y, ws) for y, ws in ref_rows if y0 <= y < y1]
        dec, integers, signs = [], [], []
        for y, ws in seg:
            line = rowtext(ws)
            # 跳过表头/辅助行（VD 工作距离、CYL 记法、S.E.、REF 标题等），
            # 否则 VD 12.00 会被误当成正球镜
            if re.search(r"\bVD\b|\bCYL\b|S\.?\s*E\.?|SHE|\bREF\b|\\bDATA\\b|VD[:：]", line, re.I):
                continue
            for w in ws:
                x = w["bbox"][0]
                t = clean(w["text"]).strip()
                if re.fullmatch(r"\d{1,2}[.:]\d{2}", t):
                    dec.append((x, y, float(t.replace(":", ".").replace(",", "."))))
                elif re.fullmatch(r"\d{2,3}", t) and 10 <= int(t) <= 180:
                    integers.append((x, y, float(t)))
                elif t in ("-", "-"):
                    signs.append((x, y))
        # 小数按 x 分成 S / C 两列
        col_groups, col_x = split_columns(dec)
        if len(col_groups) < 2:
            continue
        s_col, c_col = col_groups[0], col_groups[1]
        sx_ref = col_x[0] if col_x else 0
        # 符号：就近同列负号
        s_vals = sorted([(v[1], (-v[2] if is_neg(v[0], v[1], signs) else v[2])) for v in s_col])
        c_vals = sorted([(v[1], v[2]) for v in c_col])
        a_vals = sorted((v[1], v[2]) for v in integers)
        # 按 y 就近配对 S/C/A；配对数为每行一条
        pairs = []
        for sy, sv in s_vals:
            cy, cv = min(c_vals, key=lambda c: abs(c[0] - sy)) if c_vals else (sy, None)
            ay, av = min(a_vals, key=lambda a: abs(a[0] - sy)) if a_vals else (sy, None)
            if cv is not None:
                pairs.append((sv, cv, av))
        if not pairs:
            continue
        # 代表值：取球镜最负的一条（近视最深，保守）；原始多次测量结构化保留
        rep = min(pairs, key=lambda p: p[0])
        sv, cv, av = rep
        se_side = se_by_side.get(side)
        if se_side is None:
            se_side = round(sv + cv, 2)
        if raw_out is not None:
            raw_out.setdefault("REF", {})[side] = {
                "measurements": [
                    {"s": round(s, 2), "c": round(c, 2), "a": int(a) if a is not None else None}
                    for s, c, a in sorted(pairs)
                ],
                "se": round(se_side, 2),
                "representative": {"s": round(sv, 2), "c": round(cv, 2),
                                   "a": int(av) if av is not None else None},
            }
        out.append({"indicator_code": "REF_SPH", "side": side, "value_num": sv,
                    "unit": "D", "raw_value": f"{sv:+.2f}", "note": "电脑验光(未散瞳)"})
        out.append({"indicator_code": "REF_CYL", "side": side, "value_num": cv,
                    "unit": "D", "raw_value": f"{cv:+.2f}", "note": "电脑验光(未散瞳,正柱镜)"})
        if av is not None:
            out.append({"indicator_code": "REF_AXIS", "side": side, "value_num": av,
                        "unit": "°", "raw_value": str(int(av)), "note": "电脑验光轴位"})
        out.append({"indicator_code": "REF_SE", "side": side, "value_num": se_side,
                    "unit": "D", "raw_value": f"{se_side:+.2f}",
                    "note": "电脑验光S.E.(未散瞳，不可直接作远视储备)"})

    # ---- PD ----
    if y_pd is not None:
        for y, ws in rows:
            if y == y_pd:
                m = re.search(r"PD\s*[:：]?\s*(\d{2}\.\d)", rowtext(ws), re.I)
                if m:
                    out.append({"indicator_code": "REF_PD", "side": "OU",
                                "value_num": _num(m.group(1)), "unit": "mm", "raw_value": m.group(1)})
                    if raw_out is not None:
                        raw_out["pd"] = _num(m.group(1))
                break

    # ---- KRT 段：按 x 动态分 D/MM/A 列；按顺序每眼 R1/R2/AVE ----
    if y_krt is not None:
        krt_rows = [(y, ws) for y, ws in rows if y > y_krt]
        dvals, mmvals = [], []
        cyls = []
        for y, ws in krt_rows:
            for w in ws:
                x = w["bbox"][0]
                t = clean(w["text"]).strip()
                if re.fullmatch(r"\d{2}\.\d{2}", t):
                    v = float(t.replace(":", "."))
                    if 35 <= v <= 50:
                        dvals.append((x, y, v))
                    elif 5 <= v <= 12:
                        mmvals.append((x, y, v))
            t = rowtext(ws)
            cm = re.search(r"CYL\s*[:：]?\s*([-+]?\d{1,2}[.:]\d{2})", t, re.I)
            if cm:
                cyls.append(abs(_num(cm.group(1).replace(":", "."))))
        dvals.sort(key=lambda v: v[1])
        # 每眼取前两个 D 值为 K1/K2（R1/R2），第三个 AVE 跳过
        for i, side in enumerate(("OD", "OS")):
            eye_d = dvals[i * 3:i * 3 + 3]
            if len(eye_d) >= 1:
                out.append({"indicator_code": "BIO_K1", "side": side, "value_num": eye_d[0][2],
                            "unit": "D", "raw_value": f"{eye_d[0][2]:.2f}", "note": "电脑验角膜曲率R1"})
            if len(eye_d) >= 2:
                out.append({"indicator_code": "BIO_K2", "side": side, "value_num": eye_d[1][2],
                            "unit": "D", "raw_value": f"{eye_d[1][2]:.2f}", "note": "电脑验角膜曲率R2"})
            if i < len(cyls) and 0 <= cyls[i] <= 10:
                out.append({"indicator_code": "BIO_AST", "side": side, "value_num": cyls[i],
                            "unit": "D", "raw_value": f"{cyls[i]:.2f}", "note": "角膜散光(电脑验光)"})
            if raw_out is not None:
                krt = raw_out.setdefault("KRT", {})[side] = {
                    "r1": round(eye_d[0][2], 2) if len(eye_d) >= 1 else None,
                    "r2": round(eye_d[1][2], 2) if len(eye_d) >= 2 else None,
                    "ave": round(eye_d[2][2], 2) if len(eye_d) >= 3 else None,
                    "cyl": round(cyls[i], 2) if i < len(cyls) else None,
                }
    return out


# ---------------------------------------------------------------------------
# NIDEK ARK-1 电脑验光/角膜曲率小票专用解析器
# 版式：右眼 REF(3 次 + 平均行) → PS → 右眼 KRT(R1/R2/AVG/CYL) →
#       左眼 REF(3 次 + 平均行) → PS → 左眼 KRT → CS → PD
# 常规 OCR 会把 "-0.30" 读成 "-9.30"，改用 ocr_engine 放大重扫文本解析。
# ---------------------------------------------------------------------------

def is_nidek_ark(text: str, enhanced: str = "") -> bool:
    t = f"{text or ''}\n{enhanced or ''}"
    if re.search(r"TOPCON|REF\.DATA|KRT\.DATA", t, re.I):
        return False
    if re.search(r"NIDEK|ARK[-\s]?1", t, re.I):
        return True
    return bool(re.search(r"\bPS\s*\d", t, re.I) and
                re.search(r"R1\s+\d+\.\d+|<\s*R1", t, re.I) and
                re.search(r"CYL|GNE|AVG|CAVG", t, re.I))


def _nidek_norm(s: str) -> str:
    s = (s.replace("\u2212", "-").replace("−", "-").replace("—", "-")
           .replace("–", "-").replace("=", "-").replace(":", "."))
    s = re.sub(r"([+-])\s+", r"\1", s)
    s = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_nidek_ark(text, enhanced, warnings, raw_out, cycloplegic=False):
    """解析 NIDEK ARK-1 小票。

    代表值取仪器打印的平均行（常规 OCR 与放大重扫互补，纠正 O→0、
    0 误识为 9），多次测量尽量保留；KRT/PD 取放大重扫文本。
    """
    def norm(s):
        s = (s.replace("\u2212", "-").replace("−", "-").replace("—", "-")
              .replace("–", "-").replace("=", "-"))
        s = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", s)
        s = re.sub(r"([+-])\s+", r"\1", s)
        return re.sub(r"\s+", " ", s).strip()

    def avg_from_line(raw):
        s = raw.replace("¢", "<").replace("O", "0").replace("o", "0")
        if not re.search(r"[<>]", s) or re.search(r"R1|R2|AVG|CYL|GN|VD|PS|deg|CS", s, re.I):
            return None
        nums = [(m.group(1), m.group(2), m.group(3))
                for m in re.finditer(r"([+-]?)\s*O?(\d)[.,]?(\d{2})(?!\d)", s)]
        if len(nums) < 2:
            return None

        def val(t):
            v = float(f"{t[1]}.{t[2]}")
            if 6 < v < 10:          # OCR 把 0 读成 9："-9.30" 实为 "-0.30"
                v = round(v - 9, 2)
            return -v if t[0] == "-" else v

        sv, cv = val(nums[0]), val(nums[1])
        if not (-12 <= sv <= 12 and -6 <= cv <= 6):
            return None
        axes = krt_axes(s)
        return {"s": round(sv, 2), "c": round(cv, 2), "a": axes[-1] if axes else None}

    def segs(blob):
        ls = [norm(l) for l in (blob or "").splitlines() if l.strip()]
        idx = [i for i, l in enumerate(ls) if re.match(r"^[^\d]*PS\b", l, re.I)]
        if not idx:
            return [], []
        if len(idx) == 1:       # 放大重扫常漏掉第二个 PS，按唯一 PS 分眼
            return ls[:idx[0]], ls[idx[0] + 1:]
        return ls[:idx[0]], ls[idx[0] + 1:idx[1]]

    def krt_nums(s):
        out = []
        for m in re.finditer(r"(?<![\d.])([+-]?)(\d{1,2})[.,\-](\d{1,2})(?![\d])", s):
            v = float(f"{m.group(2)}.{m.group(3)}")
            out.append(-v if m.group(1) == "-" else v)
        for m in re.finditer(r"(?<![\d.])(\d{4})(?![\d])", s):  # "4224" → 42.24
            v = int(m.group(1))
            if 3500 <= v <= 5000:
                out.append(v / 100)
        return out

    def krt_axes(s):
        return [int(m.group(1)) for m in re.finditer(r"(?<![\d.,])(\d{2,3})(?![\d.,])", s)
                if 10 <= int(m.group(1)) <= 180 and int(m.group(1)) != 9]

    def parse_krt(seg):
        kv = {"r1": None, "r2": None, "ave": None, "cyl": None,
              "r1_axis": None, "r2_axis": None, "cyl_axis": None}
        slot = 0
        for l in seg:
            if re.search(r"VD|PS|deg|CS\s|PD", l, re.I):
                continue
            nums, axes = krt_nums(l), krt_axes(l)
            mm = next((n for n in nums if 5 <= n < 12), None)
            d = next((n for n in nums if 35 <= n <= 50), None)
            cyln = next((abs(n) for n in nums if 0 < abs(n) <= 4), None)
            if mm is not None and d is not None:
                key = ("r1", "r2", "ave")[min(slot, 2)]
                kv[key] = round(d, 2)
                kv[key + "_axis"] = axes[0] if axes else None
                slot += 1
            elif cyln is not None and axes:
                kv["cyl"] = round(cyln, 2)
                kv["cyl_axis"] = axes[-1]
                break  # CYL 是本眼 KRT 最后一行，之后即下一眼 REF
        return kv

    # 双眼代表值：常规 OCR 与增强 OCR 互补
    reps, ref_segs = {}, {"OD": [], "OS": []}
    for blob in (text, enhanced):
        s1, s2 = segs(blob)
        for side, seg in (("OD", s1), ("OS", s2)):
            ref_segs[side] = ref_segs[side] + list(seg)
            if side in reps:
                continue
            for l in seg:
                r = avg_from_line(l)
                if r:
                    reps[side] = r
                    break

    # KRT：按 "D deg" 表头分眼（R1 常被 OCR 成 R)/RI，不可靠）；双文本源互补
    def krt_segments(blob):
        ls = [norm(l) for l in (blob or "").splitlines() if l.strip()]
        di = [i for i, l in enumerate(ls) if re.search(r"deg", l, re.I)]
        if len(di) < 2:
            return [], []
        return ls[di[0] + 1:di[1]], ls[di[1] + 1:]

    krt_by_side = {"OD": {}, "OS": {}}
    for blob in (text, enhanced):
        k1, k2 = krt_segments(blob)
        for side, seg in (("OD", k1), ("OS", k2)):
            parsed = parse_krt(seg)
            for key, value in parsed.items():
                if krt_by_side[side].get(key) is None and value is not None:
                    krt_by_side[side][key] = value

    out = []
    note_sph = "散瞳电脑验光" if cycloplegic else "电脑验光(未散瞳)"
    note_se = ("散瞳电脑验光S.E.，可作远视储备参考" if cycloplegic
               else "电脑验光S.E.(未散瞳，不可直接作远视储备)")
    for i, side in enumerate(("OD", "OS")):
        rep = reps.get(side)
        if rep is None:
            continue
        sv, cv, av = rep["s"], rep["c"], rep["a"]
        se = round(sv + cv / 2, 2)
        raw_out.setdefault("REF", {})[side] = {
            "measurements": [], "representative": rep, "se": se,
        }
        # 多次测量原值（兼容粘连 "+011+0.30"、"/" 小数点、符号丢失）
        for l in ref_segs.get(side, []):
            if re.search(r"[<>]|VD|PS|deg|R1|R2|AVG|CYL|CS|NIDEK|ARK", l, re.I):
                continue
            glue = re.search(r"([+-]?)0(\d{2})([+-])0?[.,]?(\d{2})\s+(\d{2,3})", l)
            if glue:
                gsv = float(f"0.{glue.group(2)}")
                gcv = float(f"0.{glue.group(4)}")
                if glue.group(1) == "-":
                    gsv = -gsv
                if glue.group(3) == "-":
                    gcv = -gcv
                item = {"s": round(gsv, 2), "c": round(gcv, 2), "a": int(glue.group(5))}
                if item not in raw_out["REF"][side]["measurements"]:
                    raw_out["REF"][side]["measurements"].append(item)
                continue
            # S/C/A 完全粘连："-9.30-0.07171" / "-0.28-012165"
            glue2 = re.search(r"([+-]?)(\d)[.,](\d{2})([+-]?)0?[.,]?(\d{2})(\d{2,3})", l)
            if glue2:
                gsv = float(f"{glue2.group(2)}.{glue2.group(3)}")
                gcv = float(f"0.{glue2.group(5)}")
                if 6 < gsv < 10:
                    gsv = round(gsv - 9, 2)
                if glue2.group(1) == "-":
                    gsv = -gsv
                if glue2.group(4) == "-":
                    gcv = -gcv
                item = {"s": round(gsv, 2), "c": round(gcv, 2), "a": int(glue2.group(6))}
                if item not in raw_out["REF"][side]["measurements"]:
                    raw_out["REF"][side]["measurements"].append(item)
                continue
            nums = [(m.group(1), float(f"{m.group(2)}.{m.group(3)}"))
                    for m in re.finditer(r"([+-]?)(\d)[.,/](\d{2})(?!\d)", l)]
            if len(nums) < 2:
                continue
            vals = []
            for idx, (sg, v) in enumerate(nums[:2]):
                if 6 < v < 10:          # 0 被读成 9
                    v = round(v - 9, 2)
                if sg == "+":
                    vals.append(v)
                elif sg == "-":
                    vals.append(-v)
                else:
                    base = rep["s"] if idx == 0 else rep["c"]
                    vals.append(-v if base < 0 else v)
            if not (-12 <= vals[0] <= 12 and -6 <= vals[1] <= 6):
                continue
            axes = krt_axes(l)
            item = {"s": round(vals[0], 2), "c": round(vals[1], 2),
                    "a": axes[-1] if axes else None}
            if item not in raw_out["REF"][side]["measurements"]:
                raw_out["REF"][side]["measurements"].append(item)
        # 滤掉 OCR 粘连/串列产生的离群行（真实多次测量彼此很接近）
        raw_out["REF"][side]["measurements"] = [
            m for m in raw_out["REF"][side]["measurements"]
            if (abs(m["s"] - sv) <= 0.25 and abs(m["c"] - cv) <= 0.25 and
                (m["a"] is None or av is None or abs(m["a"] - av) <= 30))
        ]
        out.append({"indicator_code": "REF_SPH", "side": side, "value_num": sv,
                    "unit": "D", "raw_value": f"{sv:+.2f}", "note": note_sph})
        out.append({"indicator_code": "REF_CYL", "side": side, "value_num": cv,
                    "unit": "D", "raw_value": f"{cv:+.2f}", "note": note_sph})
        if av is not None:
            out.append({"indicator_code": "REF_AXIS", "side": side, "value_num": av,
                        "unit": "°", "raw_value": str(int(av)), "note": "电脑验光轴位"})
        out.append({"indicator_code": "REF_SE", "side": side, "value_num": se,
                    "unit": "D", "raw_value": f"{se:+.2f}", "note": note_se})
        kv = krt_by_side.get(side, {})
        raw_out.setdefault("KRT", {})[side] = kv
        for code, key, label in (("BIO_K1", "r1", "角膜曲率R1"),
                                 ("BIO_K2", "r2", "角膜曲率R2")):
            if kv.get(key) is not None:
                out.append({"indicator_code": code, "side": side, "value_num": kv[key],
                            "unit": "D", "raw_value": f"{kv[key]:.2f}", "note": label})
        if kv.get("cyl") is not None:
            out.append({"indicator_code": "BIO_AST", "side": side, "value_num": kv["cyl"],
                        "unit": "D", "raw_value": f"{kv['cyl']:.2f}", "note": "角膜散光(ARK-1)"})

    pd_text = " ".join([norm(l) for l in (enhanced or "").splitlines() if l.strip()]) + " " + norm(text or "")
    pm = re.search(r"\bP[DP]\s*(\d{2})(?:\.\d)?", pd_text, re.I)
    if pm:
        pd = float(pm.group(1))
        if 45 <= pd <= 85:
            out.append({"indicator_code": "REF_PD", "side": "OU", "value_num": pd,
                        "unit": "mm", "raw_value": pm.group(1), "note": "瞳距(仪器打印)"})
            raw_out["pd"] = pd
    if not out:
        warnings.append("NIDEK 小票未取到有效指标，请核对原图")
    return out


def is_cn_manifest(text: str, enhanced: str = "") -> bool:
    if ("显然验光" in text or "验光检查报告单" in text or
            ("矫正视力" in text and "眼别" in text) or "显然验光" in enhanced):
        return True
    # 拍照件常规 OCR 常只剩机构名；结合增强文本里的表格词判断
    enh_compact = re.sub(r"\s+", "", enhanced)
    if "医院" in text and re.search(
            r"平[光交]|-\s*0\s*[.,]\s*\d|\b0[.,]0|矫正|SPH|CYC|AX轴|球镜|柱镜|验光|1[.,]0",
            enh_compact, re.I):
        return True
    return False


def _parse_cn_manifest(page, warnings):
    """解析中文显然验光报告单。返回字段列表。"""
    text = page.get("text", "") or ""
    enhanced = page.get("enhanced_text", "") or ""
    blob = text + "\n" + enhanced

    def clean(s):
        return re.sub(r"\s+", "", s).replace("−", "-")

    compact = clean(blob)   # 去所有空白，容错 OCR 把 "-0. 50" 读成带空格
    out = []

    # 日期（含中文"检查日期"）
    # 眼别：R/L 行。中文单常写 "眼别"，OD/OS 也可能出现
    def side_of_context(pattern):
        m = re.search(pattern, blob, re.I)
        return m.group(1).upper() if m else None

    # 右眼：R 或 OD 后跟 平光/数值；优先用增强文本里的明确模式
    # 匹配 "R ... 平光 ... 1.0" 与 "L ... -0.50 ... 1.0"
    def extract_eye(side_pat):
        # 在 blob 中找眼别标记位置，取其后的窗口
        m = re.search(side_pat, blob, re.I)
        if not m:
            return {}
        window = blob[m.end(): m.end() + 220]
        wc = clean(window)
        res = {}
        # 球镜：平光 / plano / 数值(带符号)
        pm = re.match(r"(平光|plano|0\.00|0)", wc, re.I)
        if pm:
            res["SPH"] = 0.0
            res["SPH_raw"] = pm.group(1)
        else:
            sm = re.search(r"([-+]?\d{1,2}\.\d{2})", wc)
            if sm:
                res["SPH"] = _num(sm.group(1))
                res["SPH_raw"] = sm.group(1)
        # 矫正视力：1.0 / 0.8 等（窗口里 0.8~2.0 的小数）
        vm = re.search(r"(?:矫正|视力|BCVA)?\s*(1\.0|0\.[68]|1\.2|2\.0)", window)
        if vm:
            res["BCVA"] = _num(vm.group(1))
        return res

    r = extract_eye(r"(?:^|\n|眼别)\s*[|]?\s*[Rr右]\b|<\s*R\s*>|\bOD\b")
    l = extract_eye(r"(?:^|\n|眼别)\s*[|]?\s*[Ll左]\b|<\s*L\s*>|\bOS\b")

    # 兜底：从整段去空格文本里取
    # 平光/平交(0D) 属右眼；带负号的小数(如 -0.50)属左眼；1.0 为双眼矫正视力
    if "SPH" not in r and re.search(r"平[光交]|plano", compact, re.I):
        r = {"SPH": 0.0, "SPH_raw": "平光"}
    if "SPH" not in l:
        neg = re.findall(r"-(\d{1,2}\.\d{2})", compact)
        if neg:
            l = {"SPH": -float(neg[0]), "SPH_raw": "-" + neg[0]}
    # 矫正视力 1.0（去空格后）
    bcva = re.search(r"1[.,]0|0[.,][68]", compact)
    if bcva:
        v = float(bcva.group(0).replace(",", "."))
        if "BCVA" not in r:
            r["BCVA"] = v
        if "BCVA" not in l:
            l["BCVA"] = v

    for side, d in (("OD", r), ("OS", l)):
        if "SPH" in d and -25 <= d["SPH"] <= 25:
            out.append({"indicator_code": "REF_SPH", "side": side, "value_num": d["SPH"],
                        "unit": "D", "raw_value": str(d.get("SPH_raw", d["SPH"])),
                        "note": "显然验光(未散瞳)"})
        if "BCVA" in d and 0.1 <= d["BCVA"] <= 2.0:
            out.append({"indicator_code": "VA_BCVA", "side": side, "value_num": d["BCVA"],
                        "unit": "decimal", "raw_value": str(d["BCVA"]), "note": "矫正视力"})
    if out:
        warnings.append("显然验光单为拍照件且反光，抽取值请务必对照原件复核")
    return out


# ---------------------------------------------------------------------------
# IOLMaster / Lenstar 眼生物测量专用解析器（双栏版式）
# 这类报告左右眼分列排版，通用逐行规则会把两栏混在一起、且误把噪声当球镜。
# 用 Tesseract 输出的词坐标先分栏，再逐栏按设备固定字段解析。
# ---------------------------------------------------------------------------

def is_biometry_device(text: str) -> bool:
    """识别 IOLMaster / Lenstar 等双栏眼生物测量报告"""
    t = text.upper()
    device = ("IOLMASTER" in t or "IOL MI" in t or "IOLI MI" in t or
              "CARL ZEISS" in t or "LENSTAR" in t)
    layout = ("复合" in text and "AL" in t) or ("角膜曲率值" in text) or ("生物测量" in text)
    return device and layout


def _cluster_words_by_line(words, tol=11):
    """把词按 top 坐标聚成行"""
    lines = []
    for w in sorted(words, key=lambda x: x["bbox"][1]):
        y = w["bbox"][1]
        hit = next((ln for ln in lines if abs(ln["y"] - y) <= tol), None)
        if hit is None:
            hit = {"y": y, "words": []}
            lines.append(hit)
        hit["words"].append(w)
    for ln in lines:
        ln["words"].sort(key=lambda w: w["bbox"][0])
    return sorted(lines, key=lambda l: l["y"])


def _parse_iolmaster_page(page, global_warnings):
    """解析单页 IOLMaster 报告，返回字段列表"""
    words = page.get("words") or []
    if not words:
        return []

    # 1) 找 OD/OS 大标题锚点（字号明显大于正文）
    big = [w for w in words if w["bbox"][3] >= 25 and
           re.fullmatch(r"(OD|OS|Os|oD)", w["text"].strip(), re.I)]
    od_cx = next((w["bbox"][0] + w["bbox"][2] / 2 for w in big if w["text"].upper() == "OD"), None)
    os_cx = next((w["bbox"][0] + w["bbox"][2] / 2 for w in big if w["text"].upper() == "OS"), None)
    if od_cx is None or os_cx is None:
        # 退化：用页面宽度中点
        x_split = max((w["bbox"][0] + w["bbox"][2] for w in words), default=1200) / 2
    else:
        x_split = (od_cx + os_cx) / 2

    # 2) 按 x 分栏（OD 左 / OS 右），逐行拼文本
    col_text = {"OD": [], "OS": []}
    for ln in _cluster_words_by_line(words):
        for side, key in (("OD", "L"), ("OS", "R")):
            picked = [w for w in ln["words"]
                      if (w["bbox"][0] + w["bbox"][2] / 2 < x_split) == (side == "OD")]
            if picked:
                col_text[side].append(" ".join(w["text"] for w in picked))
    col_blob = {s: "\n".join(t) for s, t in col_text.items()}

    def med(vals):
        v = sorted(vals)
        n = len(v)
        return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, 2)

    out = []
    for side in ("OD", "OS"):
        blob = col_blob[side]
        compact = re.sub(r"\s+", " ", blob)

        # 多次 AL 测量列（本栏所有 20~30mm 的读数）
        al_samples = [float(x) for x in re.findall(r"\b(2\d\.\d{1,2})\s*m?m?\b", compact)
                      if 20 <= float(x) <= 30]

        # 复合 AL：优先报告明确给出的 "复合 ... AL ... 23.79"
        m = re.search(r"复合.{0,24}?(2\d\.\d{1,2})", compact)
        al = None
        al_note = ""
        if m and 20 <= float(m.group(1)) <= 30:
            al = float(m.group(1))
            al_note = "报告复合AL(Composite AL)"
        elif al_samples:
            al = med(al_samples)
            al_note = f"复合AL未识别，取{len(al_samples)}次测量中位数，请核对原件"
            global_warnings.append(f"{side} 复合AL未被OCR识别，眼轴取多次测量中位数({al}mm)，建议人工核对")
        if al is not None:
            out.append({"indicator_code": "BIO_AL", "side": side, "value_num": al,
                        "unit": "mm", "raw_value": (m.group(1) if m and al == float(m.group(1)) else str(al)),
                        "note": al_note})

        # 平均角膜曲率 "42.51/43.72 D"（OCR 可能写成 4261/4349D）
        k1 = k2 = None
        km = re.search(r"(\d{2}[.,]\d{2})\s*/\s*(\d{2}[.,]\d{2})\s*D", compact)
        if km:
            k1 = float(km.group(1).replace(",", "."))
            k2 = float(km.group(2).replace(",", "."))
        else:
            k1m = re.search(r"K\s*1[:：]?\s*(\d{2}[.,]\d{2})", compact, re.I)
            k2m = re.search(r"K\s*2[:：]?\s*(\d{2}[.,]\d{2})", compact, re.I)
            if k1m and k2m:
                k1 = float(k1m.group(1).replace(",", "."))
                k2 = float(k2m.group(1).replace(",", "."))
        for code, val in (("BIO_K1", k1), ("BIO_K2", k2)):
            if val is not None and 35 <= val <= 50:
                out.append({"indicator_code": code, "side": side, "value_num": val,
                            "unit": "D", "raw_value": f"{val:.2f}", "note": "平均角膜曲率"})

        # 角膜散光 ΔK / AK
        km2 = re.search(r"(?:ΔK|AK|Ak)\s*[:：]?\s*(-?\d{1,2}[.,]\d{2})", compact)
        if km2:
            ast = abs(float(km2.group(1).replace(",", ".")))
            if 0 <= ast <= 10:
                out.append({"indicator_code": "BIO_AST", "side": side, "value_num": ast,
                            "unit": "D", "raw_value": km2.group(1), "note": "ΔK 绝对值"})

        # 白到白 WTW（OCR 常识别为 WIW）
        wm = re.search(r"W[IT]W\s*[:：]?\s*(\d{2}[.,]\d{1,2})", compact, re.I)
        if wm:
            wtw = float(wm.group(1).replace(",", "."))
            if 9 <= wtw <= 16:
                out.append({"indicator_code": "BIO_WTW", "side": side, "value_num": wtw,
                            "unit": "mm", "raw_value": wm.group(1), "note": "白到白(WTW)"})

        # 前房深度 ACD（若 OCR 有识别）
        am = re.search(r"ACD\s*[:：]?\s*(\d\.\d{1,2})", compact, re.I)
        if am:
            acd = float(am.group(1).replace(",", "."))
            if 1.5 <= acd <= 6:
                out.append({"indicator_code": "BIO_ACD", "side": side, "value_num": acd,
                            "unit": "mm", "raw_value": am.group(1), "note": "前房深度"})

        # AL/CR 比（有 AL 和平均曲率半径时计算）
        if al is not None and k1 and k2:
            kmean = (k1 + k2) / 2
            radius = 337.5 / kmean
            if radius > 0:
                out.append({"indicator_code": "BIO_ALCR", "side": side,
                            "value_num": round(al / radius, 3), "unit": "比值",
                            "raw_value": f"{al}/{radius:.2f}", "note": "AL/CR = 眼轴/角膜曲率半径(系统计算)"})

    # 3) 增强重扫补 ACD（表格细线导致常规 OCR 整行漏识时）
    enhanced = page.get("enhanced_text") or ""
    for line in enhanced.splitlines():
        mm = re.match(r"\s*(OD|OS)\s*[:：]\s*(.*)", line)
        if not mm:
            continue
        side = mm.group(1).upper()
        seg = mm.group(2)
        if any(f["indicator_code"] == "BIO_ACD" and f["side"] == side for f in out):
            continue
        am = re.search(r"ACD\s*[:：]?\s*(\d[.,]\d{1,2})", seg, re.I)
        if not am:
            # OCR 常把 3.68 读成 368mm / 3 68，宽松兜底
            am = re.search(r"\b(3[.,]\d{2})\s*m?m?\b", seg)
        if am:
            acd = float(am.group(1).replace(",", "."))
            if 2.0 <= acd <= 5.0:
                out.append({"indicator_code": "BIO_ACD", "side": side, "value_num": acd,
                            "unit": "mm", "raw_value": am.group(1), "note": "增强重扫补录·前房深度"})

    if not out:
        global_warnings.append("生物测量专用解析未取到指标，已回退通用规则")
    return out


def extract_fields(pages, cyclo_override=None):
    """
    输入 pages（ocr_engine.extract_pages 输出），返回汇总抽取结果。
    cyclo_override: 用户显式指定的散瞳状态（True/False）；None 表示据报告文字判断。
    """
    all_text = "\n".join(p["text"] for p in pages)
    cat, scores = classify(all_text)
    exam_date = detect_date(all_text)
    cycloplegic = (detect_cycloplegic(all_text) if cyclo_override is None
                   else bool(cyclo_override))
    institution = detect_institution(all_text)

    enhanced_all = "\n".join(p.get("enhanced_text", "") for p in pages)

    # ---- 设备/版式专用解析优先 ----
    dev_warnings = []

    # TOPCON 电脑验光小票
    if is_topcon_receipt(all_text):
        fields = []
        raw_measurements = {}
        for p in pages:
            try:
                parsed = _parse_topcon_receipt(p, dev_warnings, raw_measurements)
            except Exception as e:
                dev_warnings.append(f"第{p['page_no']}页解析异常，已跳过：{type(e).__name__}")
                parsed = []
            for f in parsed:
                f["page_no"] = p["page_no"]
                f["confidence"] = 0.9
                fields.append(f)
        if not exam_date:
            dev_warnings.append("未识别到检查日期，请在入库前补填")
        dev_warnings.append("电脑验光为未散瞳结果，等效球镜不可直接作远视储备")
        return {
            "doc_category": cat, "doc_category_name": CATEGORY_NAMES.get(cat, "未识别"),
            "category_scores": scores, "exam_date": exam_date, "cycloplegic": cycloplegic,
            "institution": institution, "fields": fields, "warnings": dev_warnings,
            "full_text": all_text[:4000], "raw_measurements": raw_measurements,
        }

    # NIDEK ARK-1 电脑验光小票（散瞳状态可由用户显式指定）
    if is_nidek_ark(all_text, enhanced_all):
        fields = []
        raw_measurements = {}
        for p in pages:
            try:
                parsed = _parse_nidek_ark(p.get("text", ""), p.get("enhanced_text", ""),
                                          dev_warnings, raw_measurements, cycloplegic)
            except Exception as e:
                dev_warnings.append(f"第{p['page_no']}页解析异常，已跳过：{type(e).__name__}")
                parsed = []
            for f in parsed:
                f["page_no"] = p["page_no"]
                f["confidence"] = 0.86
                fields.append(f)
        exam_date = exam_date or detect_date(enhanced_all)
        if not exam_date:
            dev_warnings.append("未识别到检查日期，请在入库前补填")
        if not cycloplegic:
            dev_warnings.append("电脑验光为未散瞳结果，等效球镜不可直接作远视储备")
        return {
            "doc_category": "REF", "doc_category_name": CATEGORY_NAMES["REF"],
            "category_scores": scores, "exam_date": exam_date,
            "cycloplegic": cycloplegic, "institution": institution,
            "fields": fields, "warnings": dev_warnings,
            "full_text": all_text[:4000], "raw_measurements": raw_measurements,
        }

    # 中文显然验光报告单
    if is_cn_manifest(all_text, enhanced_all):
        fields = []
        for p in pages:
            try:
                parsed = _parse_cn_manifest(p, dev_warnings)
            except Exception as e:
                dev_warnings.append(f"第{p['page_no']}页解析异常，已跳过：{type(e).__name__}")
                parsed = []
            for f in parsed:
                f["page_no"] = p["page_no"]
                f["confidence"] = 0.8
                fields.append(f)
        exam_date = exam_date or detect_date(enhanced_all) or detect_date(all_text)
        if not exam_date:
            dev_warnings.append("未识别到检查日期，请在入库前补填")
        return {
            "doc_category": "REF", "doc_category_name": CATEGORY_NAMES["REF"],
            "category_scores": scores, "exam_date": exam_date, "cycloplegic": cycloplegic,
            "institution": institution or ("北京儿童医院" if "北京儿童医院" in all_text else ""),
            "fields": fields, "warnings": dev_warnings, "full_text": all_text[:4000],
        }

    # IOLMaster 双栏报告：走专用解析，跳过通用逐行规则（避免串栏与噪声误抽）
    if is_biometry_device(all_text):
        dev_warnings = []
        dev_fields = []
        for p in pages:
            try:
                parsed = _parse_iolmaster_page(p, dev_warnings)
            except Exception as e:
                dev_warnings.append(f"第{p['page_no']}页解析异常，已跳过：{type(e).__name__}")
                parsed = []
            for f in parsed:
                f["page_no"] = p["page_no"]
                f["confidence"] = 0.86
                dev_fields.append(f)
        # 日期/散瞳等仍用通用探测
        if not exam_date:
            dev_warnings.append("未识别到检查日期，请在入库前补填")
        return {
            "doc_category": cat, "doc_category_name": CATEGORY_NAMES.get(cat, "未识别"),
            "category_scores": scores, "exam_date": exam_date,
            "cycloplegic": cycloplegic, "institution": institution,
            "fields": dev_fields, "warnings": dev_warnings,
            "full_text": all_text[:4000],
        }

    fields = []
    seen = set()

    def add(f, page_no, conf):
        key = (f["indicator_code"], f["side"], f.get("raw_value"))
        if key in seen:
            return
        seen.add(key)
        f["page_no"] = page_no
        f["confidence"] = conf
        fields.append(f)

    for p in pages:
        lines = [ln.strip() for ln in p["text"].splitlines() if ln.strip()]
        page_conf = p.get("confidence", 0.8)
        for raw_line in lines:
            line = normalize_ocr(raw_line)
            # 验光
            for f in _parse_refraction_line(line, "OU"):
                add(f, p["page_no"], round(min(page_conf + 0.12, 1.0), 3))
            # 视力
            for f in _parse_va_line(line):
                add(f, p["page_no"], round(min(page_conf + 0.10, 1.0), 3))
            # 通用关键词规则
            for code, (kws, unit, lo, hi, need_side) in RULES.items():
                v, raw, kw = _find_value(line, kws, lo, hi)
                if v is None:
                    continue
                side = _side_of(line) if need_side else "OU"
                f = {"indicator_code": code, "raw_value": raw, "value_num": v,
                     "unit": unit, "side": side, "matched_keyword": kw}
                if code == "DRY_BUT" and re.search(r"泪膜破裂", line):
                    f["note"] = "BUT"
                add(f, p["page_no"], round(min(page_conf + 0.08, 1.0), 3))

    # 计算 SE（若已有 S/C 且同眼）；报告已直接给出 SE 的，不重复生成
    reported_se_sides = {f["side"] for f in fields if f["indicator_code"] == "REF_SE"}
    sph_map = {f["side"]: f["value_num"] for f in fields if f["indicator_code"] == "REF_SPH"}
    cyl_map = {f["side"]: f["value_num"] for f in fields if f["indicator_code"] == "REF_CYL"}
    for side in set(sph_map) & set(cyl_map):
        if side in reported_se_sides:
            continue
        se = round(sph_map[side] + 0.5 * cyl_map[side], 2)
        fields.append({
            "indicator_code": "REF_SE", "raw_value": f"{sph_map[side]:+.2f} + 1/2×({cyl_map[side]:+.2f})",
            "value_num": se, "unit": "D", "side": side, "page_no": 1,
            "confidence": 0.99, "note": "系统自动计算：SE = S + 1/2 C" + ("（散瞳，可作远视储备）" if cycloplegic else "（未散瞳，不可直接作远视储备）"),
        })

    warnings = []
    # 手写/低质量拍照件：OCR 置信度极低又只抽到零星数字时，极可能是乱码误抽
    # （如手写配镜处方单被读成 "3" → REF_SPH），不做数值入库，只提示人工核对。
    mean_conf = sum(p.get("confidence", 0.8) for p in pages) / max(len(pages), 1)
    if cat == "UNK" and len(fields) <= 2 and mean_conf < 0.55:
        fields = []
        warnings.append("本报告以手写/低清晰内容为主，数值无法可靠识别；请以纸质原件为准或手动补录")

    if not exam_date:
        warnings.append("未识别到检查日期，请在入库前补填")
    if not fields:
        warnings.append("未能自动识别到结构化指标，请手动录入或上传更清晰的报告")
    if not cycloplegic and any(f["indicator_code"] == "REF_SE" for f in fields):
        warnings.append("本报告未见散瞳标记：等效球镜不可直接当作远视储备")

    return {
        "doc_category": cat,
        "doc_category_name": CATEGORY_NAMES.get(cat, "未识别"),
        "category_scores": scores,
        "exam_date": exam_date,
        "cycloplegic": cycloplegic,
        "institution": institution,
        "fields": fields,
        "warnings": warnings,
        "full_text": all_text[:4000],
    }
