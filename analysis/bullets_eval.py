"""五点评分：条数、长度、首句吸引力、信息密度、差异化、场景覆盖、合规。"""
import re

from .common import issue, jaccard, sub, tokenize, total_subs

BENEFIT_RE = re.compile(
    r"\b(easy|easily|no more|keep|keeps|save|saves|perfect for|fits|fit|organize|"
    r"protect|enjoy|hassle|clean|sturdy|simple|quick|help|helps|prevent|avoid|"
    r"without|free up|space|maximize)\b", re.I)
UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:in|inch|inches|cm|mm|ft|oz|lb|lbs|g|kg|ml|l|liter|"
    r"liters|%|years?|months?|pack|pcs|piece)\b", re.I)
SCENE_WORDS = {
    "kitchen": ["kitchen", "sink", "countertop", "dish", "cabinet"],
    "bathroom": ["bathroom", "shower", "vanity", "toilet"],
    "outdoor": ["outdoor", "patio", "garden", "balcony", "camping", "rv"],
    "office": ["office", "desk", "dorm", "study"],
    "gift": ["gift", "housewarming", "christmas", "holiday"],
    "travel": ["travel", "portable", "trip", "car"],
    "apartment": ["apartment", "small space", "rental", "compact"],
}
EXAGGERATION = ["best", "#1", "number one", "guaranteed", "cure", "fda", "lifetime",
                "100% ", "completely waterproof", "never rust", "unbreakable"]
IDEAL_MIN, IDEAL_MAX = 120, 260


def _completeness(bullets):
    n = len(bullets)
    count_score = min(n, 5) / 5 * 12
    lengths = [len(b) for b in bullets] or [0]
    avg = sum(lengths) / len(lengths)
    if IDEAL_MIN <= avg <= IDEAL_MAX:
        len_score, note = 8, "平均长度合理"
    elif 80 <= avg < IDEAL_MIN or IDEAL_MAX < avg <= 320:
        len_score, note = 5, "长度略偏离推荐区间"
    else:
        len_score, note = 2, "长度明显失衡"
    return count_score + len_score, {"count": n, "avg_chars": round(avg)}, \
        f"{n} 条，{note}"


def _hook_score(bullets):
    if not bullets:
        return 0.0, {}, "无五点内容"
    hits = 0
    for b in bullets:
        first = re.split(r"[.:：!?！？]", b, maxsplit=1)[0][:80]
        if BENEFIT_RE.search(first) or re.search(r"\d", first):
            hits += 1
    ratio = hits / len(bullets)
    return 20 * ratio, {"benefit_first_sentences": hits, "total": len(bullets)}, \
        f"{hits}/{len(bullets)} 条首句为利益点或含数字"


def _density_score(bullets):
    if not bullets:
        return 0.0, {}, "无五点内容"
    hits = sum(1 for b in bullets if UNIT_RE.search(b))
    ratio = hits / len(bullets)
    return 20 * ratio, {"with_quantified_info": hits, "total": len(bullets)}, \
        f"{hits}/{len(bullets)} 条含量化参数"


def _differentiation_score(bullets, title):
    if not bullets:
        return 0.0, {}, "无五点内容"
    t_tokens = tokenize(title)
    to_title = [jaccard(tokenize(b), t_tokens) for b in bullets]
    pairs = []
    for i in range(len(bullets)):
        for j in range(i + 1, len(bullets)):
            pairs.append(jaccard(tokenize(bullets[i]), tokenize(bullets[j])))
    avg_title = sum(to_title) / len(to_title)
    avg_pair = sum(pairs) / len(pairs) if pairs else 0.0
    overlap = (avg_title * 0.5 + avg_pair * 0.5)
    score = 20 * max(0.0, 1 - overlap * 2)
    return score, {"overlap_with_title": round(avg_title, 2),
                   "overlap_between_bullets": round(avg_pair, 2)}, \
        f"与标题重叠 {avg_title:.0%}，条目间重叠 {avg_pair:.0%}"


def _scenario_score(bullets):
    text = " ".join(bullets).lower()
    hit_groups = [g for g, words in SCENE_WORDS.items() if any(w in text for w in words)]
    return min(len(hit_groups), 4) / 4 * 10, {"scenes": hit_groups}, \
        ("覆盖场景：" + "、".join(hit_groups)) if hit_groups else "未识别到使用场景"


def _compliance_score(bullets):
    text = " ".join(bullets).lower()
    hits = [w for w in EXAGGERATION if w in text]
    return max(0, 10 - 4 * len(hits)), {"hits": hits}, \
        ("命中夸大词：" + "、".join(hits)) if hits else "未命中夸大宣称"


def evaluate_bullets(item):
    bullets = [b.strip() for b in (item.get("bullets") or []) if b and b.strip()]
    title = item.get("title") or ""
    issues = []
    if not bullets:
        return {"status": "skipped", "score": None, "subs": [],
                "issues": [issue("high", "五点内容缺失", "重新采集或人工补录", "bullets")]}

    s_comp, ev_comp, n_comp = _completeness(bullets)
    s_hook, ev_hook, n_hook = _hook_score(bullets)
    s_dens, ev_dens, n_dens = _density_score(bullets)
    s_diff, ev_diff, n_diff = _differentiation_score(bullets, title)
    s_scene, ev_scene, n_scene = _scenario_score(bullets)
    s_rule, ev_rule, n_rule = _compliance_score(bullets)

    subs = [
        sub("completeness", "完整性", 20, s_comp, ev_comp, n_comp),
        sub("hook", "首句吸引力", 20, s_hook, ev_hook, n_hook),
        sub("density", "信息密度", 20, s_dens, ev_dens, n_dens),
        sub("differentiation", "差异化", 20, s_diff, ev_diff, n_diff),
        sub("scenario", "场景覆盖", 10, s_scene, ev_scene, n_scene),
        sub("compliance", "合规", 10, s_rule, ev_rule, n_rule),
    ]

    if len(bullets) < 5:
        issues.append(issue("high", f"五点仅 {len(bullets)} 条", "补齐至 5 条", "bullets"))
    if ev_comp["avg_chars"] < IDEAL_MIN:
        issues.append(issue("medium", f"平均每条仅 {ev_comp['avg_chars']} 字符",
                            "扩充到 120–260 字符，补充参数与场景", "bullets"))
    if ev_hook["benefit_first_sentences"] <= len(bullets) // 2:
        issues.append(issue("medium", "过半条目首句是参数罗列而非利益点",
                            "改为「结果前置 + 参数支撑」写法", "bullets"))
    if ev_dens["with_quantified_info"] < len(bullets):
        issues.append(issue("low", "部分条目缺少可量化信息", "补充尺寸、材质、容量等数字", "bullets"))
    if ev_diff["overlap_with_title"] > 0.5:
        issues.append(issue("medium", "五点与标题大量重复", "补充标题未覆盖的信息", "bullets"))
    if ev_rule["hits"]:
        issues.append(issue("high", "含夸大或未授权宣称：" + "、".join(ev_rule["hits"]),
                            "删除绝对化表述", "bullets"))

    return {"status": "ok", "score": total_subs(subs), "subs": subs, "issues": issues,
            "evidence": {"count": len(bullets)}}
