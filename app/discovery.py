"""模块二：关键词 → 搜索结果候选 → 类目 BSR 校验 → 去重 → 前 5 名。

两步式工作流：
  discover_candidates  —— 只搜搜索页，返回候选列表（含缩略图），快
  discover             —— 在候选基础上做 BSR 校验 + 去重，取前 want 名
"""
import re

from . import xiyou


def discover_candidates(scraper, keywords, pages=3, per_keyword=40, on_progress=None):
    """轻量候选搜索：只走搜索页，不碰详情页 / BSR，给用户勾选用。

    返回 list[dict]：asin, title, price, rating, reviews, thumb, keyword, page, position
    """
    log = on_progress or (lambda m: None)
    out, seen = [], set()
    for kw in keywords:
        log(f"搜索关键词「{kw}」（{pages} 页）")
        try:
            cards = scraper.search(kw, pages=pages, limit=per_keyword)
        except Exception as e:
            log(f"「{kw}」搜索失败：{e}")
            continue
        for c in cards:
            if c["asin"] in seen:
                continue
            seen.add(c["asin"])
            out.append(c)
        log(f"「{kw}」拿到 {len(cards)} 个候选，累计 {len(out)} 个")
    return out


def _norm_title(t):
    words = re.findall(r"[a-z0-9]+", (t or "").lower())
    stop = {"for", "with", "and", "the", "a", "an", "of", "to", "in", "on", "by"}
    return {w for w in words if w not in stop and len(w) > 2}


def _same_variant(t1, t2):
    a, b = _norm_title(t1), _norm_title(t2)
    if not a or not b:
        return False
    return len(a & b) / max(len(a), len(b)) >= 0.8


def _cat_match(cat_name, target):
    if not target:
        return True
    last = re.split(r"[>/｜|]", target)[-1].strip().lower()
    name = (cat_name or "").lower()
    return bool(last) and (last in name or name in last)


def _light_bsr(scraper, asin):
    """无西柚凭证时，直接抓详情页 BSR（更慢但免费）。候选校验只轻试 2 次。"""
    d = scraper.fetch(asin, with_images=False, retries=1)
    ranks = d.get("bsr") or []
    if not ranks:
        return None
    best = min(ranks, key=lambda r: r["rank"])
    return {"bsr_rank": best["rank"], "bsr_category": best["category"], "leaf_id": best["category"]}


def discover(scraper, site_code, keywords, category="", bsr_max=100, pages=3,
             per_keyword=40, want=5, on_progress=None, use_xiyou=True):
    log = on_progress or (lambda m: None)
    candidates, seen = [], set()
    for kw in keywords:
        log(f"搜索关键词「{kw}」（{pages} 页）")
        try:
            cards = scraper.search(kw, pages=pages, limit=per_keyword)
        except Exception as e:
            log(f"「{kw}」搜索失败：{e}")
            continue
        for c in cards:
            if c["asin"] in seen:
                continue
            seen.add(c["asin"])
            candidates.append(c)
        log(f"「{kw}」拿到 {len(cards)} 个候选，累计 {len(candidates)} 个")

    if not candidates:
        return []

    client = None
    if use_xiyou and xiyou.available():
        try:
            client = xiyou.XiyouClient()
        except Exception as e:
            log(f"西柚不可用，改用页面 BSR：{e}")

    limit = min(len(candidates), 30)
    checked = []
    if client:
        log(f"用西柚校验前 {limit} 个候选的类目 BSR")
        for i, c in enumerate(candidates[:limit], 1):
            try:
                info = xiyou.fetch_asin_bsr(client, c["asin"], site_code)
                c.update(info)
            except Exception as e:
                log(f"  {c['asin']} BSR 查询失败：{str(e)[:60]}")
                continue
            checked.append(c)
        client.__exit__(None, None, None)
    else:
        limit = min(len(candidates), 12)
        log(f"未配置西柚，抓取前 {limit} 个候选详情页读取 BSR（较慢）")
        for i, c in enumerate(candidates[:limit], 1):
            try:
                info = _light_bsr(scraper, c["asin"])
                if not info:
                    continue
                c.update(info)
            except Exception as e:
                log(f"  {c['asin']} 详情页失败：{str(e)[:60]}")
                continue
            checked.append(c)

    # 类目聚类：没指定类目时，取候选最多的类目
    if not category and checked:
        groups = {}
        for c in checked:
            groups.setdefault((c.get("bsr_category") or "").lower(), []).append(c)
        if groups:
            best = max(groups.values(), key=len)
            category = best[0].get("bsr_category") or ""

    passed, used = [], set()
    for c in checked:
        rank = c.get("bsr_rank")
        if not rank or rank > bsr_max:
            continue
        if not _cat_match(c.get("bsr_category"), category):
            continue
        key = (c.get("bsr_category"), rank)
        if key in used:
            continue
        if any(_same_variant(c.get("title"), p.get("title")) for p in passed):
            continue
        used.add(key)
        c["reason"] = f"「{c.get('keyword')}」搜索第 {c.get('position')} 位；{c.get('bsr_category')} BSR #{rank}"
        passed.append(c)

    passed.sort(key=lambda x: x["bsr_rank"])
    log(f"BSR 校验通过 {len(passed)} 个，取前 {want}")
    return passed[:want]
