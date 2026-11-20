"""
ocr_engine.py — PDF / 图片 OCR 文字提取
优先使用本机 Tesseract (chi_sim+eng)，无需联网、无需 API Key。
"""
import hashlib
import re
import shutil
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output

from .db import UPLOADS, PAGES

ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".heic"}


def file_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_allowed(filename: str) -> bool:
    return file_ext(filename) in ALLOWED_EXT


def save_upload(content: bytes, filename: str):
    """按内容哈希保存，天然去重。返回 (相对路径, 文件类型, sha256, 绝对路径)"""
    sha = hashlib.sha256(content).hexdigest()
    ext = file_ext(filename)
    rel = f"{sha[:2]}/{sha}{ext}"
    dest = UPLOADS / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(content)
    return rel, ext.lstrip("."), sha, dest


def _preprocess(img: Image.Image) -> Image.Image:
    """轻度预处理：灰度 + 自动对比度，提升拍照件识别率"""
    img = ImageOps.exif_transpose(img)
    if img.mode != "L":
        img = img.convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    return img


def _enhanced_rescan(img: Image.Image, text: str, orig: Image.Image = None) -> str:
    """对表格细线多、字小的报告做二值化放大重扫，补回常规 OCR 整行漏识的字段。
    - IOLMaster/Lenstar：分栏补 ACD
    - 中文显然验光单：表格区多阈值重扫，补 R/L 球镜与矫正视力
    - NIDEK ARK-1 小票：右侧等宽数字区放大重扫，还原 S/C/A 与 KRT
    """
    try:
        import numpy as np
    except ImportError:
        return ""
    w, h = img.size

    # NIDEK ARK-1 电脑验光小票：右侧等宽数字区放大重扫。
    # 常规 psm6 会把 "-0.30" 读成 "-9.30"、把 S/C/A 列拆散；放大后 psm6
    # 能完整还原三段数值。TOPCON 已有专用解析，不进此分支。
    if (not re.search(r"TOPCON|REF\.DATA", text, re.I) and
            re.search(r"VD\s*=\s*12|NIDEK|ARK|\bPS\b|CS\s*\d", text, re.I)):
        try:
            src = orig if orig is not None else img
            crop = src.crop((int(w * 0.50), int(h * 0.14), int(w * 0.995), int(h * 0.80)))
            up = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
            g = ImageOps.autocontrast(up.convert("L"), cutoff=1)
            t = pytesseract.image_to_string(
                g, lang="eng", config="--psm 6 -c preserve_interword_spaces=1")
            # 小票底部 PD/机型单独重扫（主裁剪常把 "PD 60" 读丢）
            foot = src.crop((int(w * 0.50), int(h * 0.76), int(w * 0.99), int(h * 0.88)))
            fu = foot.resize((foot.width * 5, foot.height * 5), Image.LANCZOS)
            fg = ImageOps.autocontrast(fu.convert("L"), cutoff=1)
            tf = pytesseract.image_to_string(fg, lang="eng", config="--psm 6")
            if re.search(r"PD\s*\d", tf, re.I):
                t = t + "\n" + tf.strip()
            if re.search(r"\bR1\b|\bPS\b|VD|NIDEK|ARK", t, re.I):
                return t.strip()[:2500]
        except Exception:
            pass

    # 中文显然验光单（常规 OCR 常只读出"北京儿童医院"，条件放宽）
    if re.search(r"验光|医院|眼别|矫正|SPH", text, re.I):
        try:
            crop = img.crop((int(w * 0.08), int(h * 0.35), int(w * 0.85), int(h * 0.65)))
            up = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
            g = np.array(ImageOps.autocontrast(up.convert("L"), cutoff=1))
            parts = []
            for thr in (None, 190, 210):
                bw = g if thr is None else (g < thr).astype("uint8") * 255
                im = Image.fromarray(bw.astype("uint8"))
                for psm in (6, 11):
                    t = pytesseract.image_to_string(
                        im, lang="chi_sim+eng", config=f"--psm {psm} -c preserve_interword_spaces=1")
                    compact = re.sub(r"\s+", " ", t)
                    if re.search(r"平[光交]|\b0[.,]?0?0?\b|-\s*0\s*[.,]\s*\d|\b1\s*[.,]\s*0\b|SPH|CYC|AX|球|柱|轴", compact, re.I):
                        parts.append(compact)
            return "\n".join(dict.fromkeys(parts))[:1500]
        except Exception:
            return ""

    # 生物测量双栏报告
    if not re.search(r"IOL\s*M|IOLI\s*MI|LENSTAR|复合\s*AL|角膜曲率值", text, re.I):
        return ""
    try:
        parts = []
        for side, x0, x1 in (("OD", 0, w // 2), ("OS", w // 2, w)):
            crop = img.crop((x0, int(h * 0.54), x1, int(h * 0.70)))
            crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
            g = np.array(ImageOps.autocontrast(crop.convert("L"), cutoff=2))
            for thr in (170, 190, 200):
                bw = Image.fromarray((g < thr).astype("uint8") * 255)
                t = pytesseract.image_to_string(bw, lang="chi_sim+eng", config="--psm 6")
                if re.search(r"ACD|3[.,][68]\d", t):
                    parts.append(f"{side}: " + t.replace("\n", " ").strip())
                    break
        return "\n".join(parts)
    except Exception:
        return ""


def ocr_pil(img: Image.Image):
    """返回 (text, words[{text,conf,bbox}], mean_conf)"""
    orig = img  # _preprocess 返回新图，原图保留给小票放大重扫
    img = _preprocess(img)
    cfg = "--psm 6 -c preserve_interword_spaces=1"
    text = pytesseract.image_to_string(img, lang="chi_sim+eng", config=cfg)
    data = pytesseract.image_to_data(img, lang="chi_sim+eng", config=cfg, output_type=Output.DICT)
    words = []
    confs = []
    for i, t in enumerate(data["text"]):
        t = (t or "").strip()
        if not t:
            continue
        try:
            c = float(data["conf"][i])
        except (ValueError, TypeError):
            c = 0.0
        if c <= 0:
            continue
        words.append({
            "text": t,
            "conf": round(c, 1),
            "bbox": [
                int(data["left"][i]), int(data["top"][i]),
                int(data["width"][i]), int(data["height"][i]),
            ],
        })
        confs.append(c)
    # 文本过少时换 psm 3 再试一次
    if len(text.strip()) < 12:
        text2 = pytesseract.image_to_string(img, lang="chi_sim+eng", config="--psm 3")
        if len(text2.strip()) > len(text.strip()):
            text = text2
    mean_conf = round(sum(confs) / len(confs) / 100, 3) if confs else 0.0
    enhanced = _enhanced_rescan(img, text, orig)
    return text.strip(), words, mean_conf, enhanced


def extract_pages(abs_path: Path, ext: str, stem: str):
    """返回 [{page_no, text, words, confidence, image_path}]"""
    pages = []
    if ext == "pdf":
        import fitz
        doc = fitz.open(abs_path)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            img_path = PAGES / f"{stem}_p{i+1}.png"
            pix.save(str(img_path))
            text, words, conf, enhanced = ocr_pil(Image.open(img_path))
            pages.append({
                "page_no": i + 1,
                "text": text,
                "words": words,
                "confidence": conf,
                "enhanced_text": enhanced,
                "image_path": f"/files/pages/{img_path.name}",
            })
        doc.close()
    else:
        text, words, conf, enhanced = ocr_pil(Image.open(abs_path))
        pages.append({
            "page_no": 1,
            "text": text,
            "words": words,
            "confidence": conf,
            "enhanced_text": enhanced,
            "image_path": f"/files/uploads/{(abs_path.relative_to(UPLOADS)).as_posix()}",
        })
    return pages
