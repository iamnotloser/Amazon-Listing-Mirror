"""图片评分：像素规则给出确定性分数；多模态作为可选的独立语义分，不参与合成。"""
import math
import os

from PIL import Image as PILImage

from .common import issue, sub, total_subs

MAIN_TARGET = 7
APLUS_TARGET = 8
MAX_SIDE = 320
BG_TOL = 20


def _analyze(path):
    """降采样后估算四角背景亮度与均匀度、长宽比、主体占比。"""
    with PILImage.open(path) as im:
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            canvas = PILImage.new("RGB", rgba.size, (255, 255, 255))
            canvas.paste(rgba, mask=rgba.split()[-1])
            im = canvas
        else:
            im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        w, h = im.size
        if not w or not h:
            return None
        px = im.load()
        step = max(1, int(((w * h) / 20000) ** 0.5))
        bx, by = max(1, int(w * 0.12)), max(1, int(h * 0.12))

        grays = []
        for cx, cy in ((0, 0), (w - bx, 0), (0, h - by), (w - bx, h - by)):
            for y in range(cy, cy + by, step):
                for x in range(cx, cx + bx, step):
                    r, g, b = px[x, y]
                    grays.append((r + g + b) / 3)

        mean = sum(grays) / len(grays) if grays else 0.0
        std = math.sqrt(sum((g - mean) ** 2 for g in grays) / len(grays)) if grays else 0.0

        minx, miny, maxx, maxy = w, h, -1, -1
        for y in range(0, h, step):
            for x in range(0, w, step):
                r, g, b = px[x, y]
                gray = (r + g + b) / 3
                is_bg = gray >= 200 and max(r, g, b) - min(r, g, b) <= 12 and abs(gray - mean) <= BG_TOL
                if not is_bg:
                    minx, miny = min(minx, x), min(miny, y)
                    maxx, maxy = max(maxx, x), max(maxy, y)
        coverage = 0.0 if maxx < minx else ((maxx - minx + 1) * (maxy - miny + 1)) / (w * h)

        return {
            "file": os.path.basename(path),
            "size": [im.width, im.height],
            "ratio": round(im.width / im.height, 3),
            "bg_brightness": round(mean, 1),
            "bg_uniformity": round(std, 1),
            "coverage": round(coverage, 3),
        }


def _bg_score(brightness, uniformity):
    if brightness >= 240 and uniformity <= 8:
        return 25, "四角接近纯白且均匀"
    if brightness >= 215 and uniformity <= 15:
        return 20, "干净的浅色影棚底，基本符合主图规范"
    if brightness >= 190 and uniformity <= 30:
        return 12, "背景偏灰或不均匀，疑似场景图"
    return 4, "背景为实景，不符合白底主图规范"


def _resolve(root, rel):
    if not rel:
        return None
    path = os.path.normpath(os.path.join(str(root), rel))
    return path if os.path.isfile(path) else None


def evaluate_images(item, collection_dir):
    images = item.get("images") or {}
    mains = [p for p in (_resolve(collection_dir, x) for x in (images.get("main") or [])) if p]
    aplus = [p for p in (_resolve(collection_dir, x) for x in (images.get("aplus") or [])) if p]
    variation = _resolve(collection_dir, images.get("variation"))
    issues, gaps, subs = [], [], []

    # 采集顺序不保证首图在前，取前 4 张里最像白底主图的一张评估规范
    candidates = [c for c in (_analyze(p) for p in mains[:4]) if c]
    best, best_pair = None, None
    for cand in candidates:
        pair = _bg_score(cand["bg_brightness"], cand["bg_uniformity"])
        if best_pair is None or pair[0] > best_pair[0]:
            best, best_pair = cand, pair

    count_score = min(len(mains), MAIN_TARGET) / MAIN_TARGET * 20
    subs.append(sub("count", "主图数量", 20, count_score,
                    {"main": len(mains), "target": MAIN_TARGET},
                    f"{len(mains)}/{MAIN_TARGET} 张"))

    if best is None:
        subs.append(sub("white_bg", "白底规范", 25, 0, {"bg_brightness": None}, "无可用主图"))
        subs.append(sub("aspect", "图片比例", 15, 0, {"ratio": None}, "无可用主图"))
        subs.append(sub("coverage", "产品占比", 15, 0, {"coverage": None}, "无可用主图"))
        issues.append(issue("high", "没有主图文件", "检查采集是否下载成功", "images"))
    else:
        ws, wn = best_pair
        subs.append(sub("white_bg", "白底规范", 25, ws,
                        {"image": best["file"], "brightness": best["bg_brightness"],
                         "uniformity": best["bg_uniformity"]}, wn))

        ratio = best["ratio"]
        dev = abs(ratio - 1.0)
        if dev <= 0.03:
            rs, rn = 15, "接近 1:1"
        elif dev <= 0.10:
            rs, rn = 10, "轻微非正方形"
        elif dev <= 0.20:
            rs, rn = 5, "明显非正方形"
        else:
            rs, rn = 2, "长宽比失衡"
        subs.append(sub("aspect", "图片比例", 15, rs, {"ratio": ratio}, rn))

        cov = best["coverage"]
        if 0.55 <= cov <= 0.95:
            cs, cn = 15, "主体占比合理"
        elif 0.40 <= cov < 0.55:
            cs, cn = 11, "主体偏小，留白过多"
        elif 0.95 < cov <= 1.0:
            cs, cn = 8, "主体几乎填满画布，缺少留白"
        else:
            cs, cn = 6, "主体占比失衡"
        subs.append(sub("coverage", "产品占比", 15, cs, {"coverage": cov}, cn))

    aplus_score = min(len(aplus), APLUS_TARGET) / APLUS_TARGET * 20
    subs.append(sub("aplus", "A+ 图数量", 20, aplus_score,
                    {"aplus": len(aplus), "target": APLUS_TARGET},
                    f"{len(aplus)}/{APLUS_TARGET} 张"))
    if not aplus:
        issues.append(issue("high", "无 A+ 图", "补充 A+ 模块以提升转化", "images"))
    elif len(aplus) < 4:
        issues.append(issue("medium", f"A+ 仅 {len(aplus)} 张，模块偏少", "补充品牌故事与对比模块", "images"))

    subs.append(sub("variation", "变体呈现", 5, 5 if variation else 0,
                    {"has_variation_image": bool(variation)},
                    "有变体截图" if variation else "无变体截图，可能本身无变体"))
    if not variation:
        gaps.append("变体截图缺失（可能该 ASIN 无变体）")
    if len(mains) < MAIN_TARGET:
        issues.append(issue("medium", f"主图仅 {len(mains)} 张", "补齐至 7 张，覆盖场景、细节、尺寸", "images"))
    if best and best_pair[0] <= 12:
        issues.append(issue("medium", "未找到白底主图候选", "确认首图使用纯白背景，避免场景图作主图", "images"))

    return {
        "status": "ok" if mains or aplus else "skipped",
        "rule_score": total_subs(subs),
        "subs": subs,
        "issues": issues,
        "data_gaps": gaps,
        "evidence": {"main": len(mains), "aplus": len(aplus),
                     "analyzed_image": best, "candidates": [c["file"] for c in candidates]},
    }
