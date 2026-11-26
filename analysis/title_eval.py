"""标题评分：只吃 title / title_main / title_highlights / brand，输出可追溯的子项。"""
import re

from .common import issue, jaccard, sub, tokenize, total_subs

BAD_PHRASES = [
    "best seller", "#1", "number 1", "no.1", "guaranteed", "fda approved",
    "cure", "free shipping", "100% waterproof", "lifetime warranty",
]
SEP_RE = re.compile(r"[,，|｜\-–—]")
PROMO_RE = re.compile(r"\b(sale|deal|free shipping|hot|new|cheap)\b", re.I)


def _length_score(chars):
    if 80 <= chars <= 200:
        return 15, "长度处于 80–200 推荐区间"
    if 200 < chars <= 230:
        return 12, "略超 200 字符，移动端可能被截断"
    if chars > 230:
        return 9, "明显超长，移动端截断风险高"
    if 60 <= chars < 80:
        return 10, "偏短，关键词覆盖空间未用满"
    if 40 <= chars < 60:
        return 6, "过短，信息量不足"
    return 3, "严重过短，几乎无法承载关键词"


def _structure_score(title, main, brand):
    score, notes = 0.0, []
    low = (title or "").lower()
    if brand and low.startswith(brand.lower()):
        score += 10
        notes.append("品牌前置")
    elif brand and brand.lower() in low[:40]:
        score += 6
        notes.append("品牌靠前但未首位")
    else:
        notes.append("未检测到品牌名")
    head = " ".join((main or title or "").split()[:5])
    if head and not PROMO_RE.search(head):
        score += 5
        notes.append("前 5 词无促销词")
    if len(SEP_RE.findall(title or "")) >= 1:
        score += 5
        notes.append("有分段结构")
    return score, "；".join(notes)


def _highlight_score(main, highlights):
    if not highlights:
        return 0.0, {"overlap": None}, "未从标题中拆出亮点，无法判断互补性"
    overlap = jaccard(tokenize(main), tokenize(highlights))
    base = 8
    if overlap <= 0.5:
        base += 12
        note = "亮点与主标题互补，信息增量明显"
    elif overlap <= 0.75:
        base += 7
        note = "亮点与主标题有部分重叠"
    else:
        base += 2
        note = "亮点几乎重复主标题，未提供新信息"
    return base, {"overlap": round(overlap, 2)}, note


def _repetition_score(text, brand):
    tokens = tokenize(text)
    if not tokens:
        return 0.0, {}, "无有效词"
    if len(tokens) < 8:
        # 词太少时重复率没有统计意义，交由长度项扣分，避免误判为堆砌
        return 20, {"tokens": len(tokens)}, "标题过短，重复度不参与判断"
    ignore = set(tokenize(brand or ""))
    counts = {}
    for t in tokens:
        if t in ignore:
            continue
        counts[t] = counts.get(t, 0) + 1
    peak = max(counts.values()) if counts else 0
    ratio = peak / len(tokens)
    if ratio <= 0.12:
        score, note = 20, "核心词分布自然"
    elif ratio <= 0.2:
        score, note = 14, "个别词重复偏多"
    elif ratio <= 0.3:
        score, note = 8, "存在明显堆砌"
    else:
        score, note = 4, "严重重复堆砌"
    return score, {"peak_repeat": peak, "tokens": len(tokens),
                   "repeat_ratio": round(ratio, 2)}, note


def _readability_score(title):
    words = (title or "").split()
    score = 15.0
    notes = []
    if words:
        caps = sum(1 for w in words if len(w) > 2 and w.isupper())
        caps_ratio = caps / len(words)
        if caps_ratio > 0.35:
            score -= 6
            notes.append("全大写词过多")
        elif caps_ratio > 0.2:
            score -= 3
            notes.append("全大写词偏多")
        avg_len = sum(len(w) for w in words) / len(words)
        if avg_len > 9:
            score -= 4
            notes.append("平均词长过长")
        elif avg_len > 7.5:
            score -= 2
            notes.append("词长偏长")
    symbols = len(re.findall(r"[!！★✔✅🔥]", title or ""))
    if symbols >= 2:
        score -= 4
        notes.append(f"特殊符号 {symbols} 个")
    elif symbols == 1:
        score -= 2
        notes.append("含 1 个特殊符号")
    return max(score, 0), {"allcaps_ratio": round(caps_ratio, 2) if words else 0}, \
        "；".join(notes) or "可读性良好"


def _compliance_score(title):
    low = (title or "").lower()
    hits = [p for p in BAD_PHRASES if p in low]
    score = max(0, 10 - 3 * len(hits))
    return score, {"hits": hits}, ("命中风险词：" + "、".join(hits)) if hits else "未命中风险词"


def evaluate_title(item):
    title = (item.get("title") or "").strip()
    main = (item.get("title_main") or title).strip()
    highlights = (item.get("title_highlights") or "").strip()
    brand = (item.get("brand") or "").strip()
    issues = []
    if not title:
        return {"status": "skipped", "score": None, "subs": [],
                "issues": [issue("high", "标题缺失", "重新采集该 ASIN", "title")]}

    chars = len(title)
    s_len, n_len = _length_score(chars)
    s_len = sub("length", "长度适配", 15, s_len, {"chars": chars}, n_len)

    s_str, n_str = _structure_score(title, main, brand)
    s_str = sub("structure", "结构完整性", 20, s_str,
                {"brand": brand or None, "first_words": " ".join(main.split()[:5])}, n_str)

    s_hl, ev_hl, n_hl = _highlight_score(main, highlights)
    s_hl = sub("highlight", "亮点互补性", 20, s_hl,
               {"highlights": highlights or None, **ev_hl}, n_hl)

    s_rep, ev_rep, n_rep = _repetition_score(title, brand)
    s_rep = sub("repetition", "关键词自然度", 20, s_rep, ev_rep, n_rep)

    s_read, ev_read, n_read = _readability_score(title)
    s_read = sub("readability", "可读性", 15, s_read, ev_read, n_read)

    s_comp, ev_comp, n_comp = _compliance_score(title)
    s_comp = sub("compliance", "合规风险", 10, s_comp, ev_comp, n_comp)

    subs = [s_len, s_str, s_hl, s_rep, s_read, s_comp]

    if chars > 230:
        issues.append(issue("medium", f"标题 {chars} 字符，超出建议上限", "压缩至 200 字符内，保留核心词", "title"))
    if not highlights:
        issues.append(issue("low", "标题未拆出亮点段", "补充场景、材质、人群等增量信息", "title"))
    if s_hl["evidence"].get("overlap") is not None and s_hl["evidence"]["overlap"] > 0.75:
        issues.append(issue("medium", "亮点与主标题高度重复", "改写为差异化的使用场景或材质卖点", "title"))
    if ev_comp["hits"]:
        issues.append(issue("high", "标题含平台风险词：" + "、".join(ev_comp["hits"]),
                            "删除绝对化或未授权宣称", "title"))

    return {"status": "ok", "score": total_subs(subs), "subs": subs, "issues": issues,
            "evidence": {"chars": chars, "words": len(title.split())}}
