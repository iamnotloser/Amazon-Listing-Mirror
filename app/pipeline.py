"""业务编排：模块一（ASIN → 模板表）、模块二（关键词 → BSR Top5 → 模板表）。"""
import glob
import os
import random
import re
import time
import zipfile

from . import discovery, media, xiyou
from .collection_store import save_collection
from .excel_writer import build_workbook
from .scraper import AmazonScraper
from .sites import get_site

APLUS_LIMIT = 8
MAIN_LIMIT = 7


def parse_asins(text):
    raw = re.split(r"[,\s;，、]+", (text or "").strip())
    out, bad = [], []
    for x in raw:
        x = x.strip().upper()
        if not x:
            continue
        if re.fullmatch(r"B0[A-Z0-9]{8}", x):
            if x not in out:
                out.append(x)
        else:
            bad.append(x)
    return out, bad


def _enrich(item, scraper, img_root, sales_client=None, site_code="US", progress=None):
    log = progress or (lambda m: None)
    asin = item["asin"]
    folder = os.path.join(img_root, asin)
    os.makedirs(folder, exist_ok=True)

    shot = os.path.join(folder, "variation.png")
    data = item.get("_scraped")
    if not data:
        data = scraper.fetch(asin, with_images=True, variation_shot=shot, log=log)
    if not data.get("images"):
        log(f"  {asin}: 图片为空，重新抓取一次")
        data = scraper.fetch(asin, with_images=True, variation_shot=shot, log=log)
    data.update({k: item.get(k) for k in ("reason", "keyword", "position") if item.get(k)})

    mains = media.download_set(data.get("images") or [], folder, "main", MAIN_LIMIT)
    aplus = media.download_set(data.get("aplus_images") or [], folder, "aplus", APLUS_LIMIT)
    if not mains:
        mains = sorted(glob.glob(os.path.join(folder, "main_*")))
    if not aplus:
        aplus = sorted(glob.glob(os.path.join(folder, "aplus_*")))
    log(f"  {asin}: 主图 {len(mains)} 张，A+ {len(aplus)} 张")

    data["main_local"] = mains
    data["aplus_local"] = aplus
    if os.path.exists(shot):
        data["variation_local"] = shot

    if sales_client is not None:
        try:
            sales = xiyou.fetch_asin_sales(sales_client, asin, site_code)
            if sales not in (None, ""):
                data["sales_30d"] = sales
                log(f"  {asin}: 已补近30天销量 {sales}")
        except Exception as e:
            log(f"  {asin}: 销量补充失败 {str(e)[:50]}")
    return data


def _finish(items, site_code, outdir, meta, progress, source, request=None):
    log = progress or (lambda m: None)
    saved = save_collection(outdir, items, site_code, source, request)
    log(f"结构化资料包已生成：data.json（{saved['count']} 个 ASIN）")

    log("生成 Excel（嵌入图片）")
    xlsx = os.path.join(outdir, f"竞品LISTING分析_{time.strftime('%Y%m%d_%H%M')}.xlsx")
    build_workbook(items, xlsx, meta)
    log(f"Excel 已生成：{os.path.basename(xlsx)}")

    zip_path = os.path.join(outdir, "打包下载.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        # 对外只放 Excel 与图片；结构化数据留在服务端供分析模块读取
        z.write(xlsx, os.path.basename(xlsx))
        img_root = os.path.join(outdir, "images")
        if os.path.isdir(img_root):
            for root, dirs, files in os.walk(img_root):
                dirs[:] = [d for d in dirs if d != "_thumb"]
                for f in files:
                    if f.endswith((".py", ".pyc", ".tmp", ".json")):
                        continue
                    p = os.path.join(root, f)
                    z.write(p, os.path.relpath(p, outdir))
    return {"xlsx": xlsx, "zip": zip_path, "count": len(items),
            "collection_id": saved["collection_id"], "data": saved["data"],
            "manifest": saved["manifest"]}


def run_asin_job(asins, site_code, outdir, progress=None, use_xiyou=True,
                 keywords=None, category=None):
    log = progress or (lambda m: None)
    get_site(site_code)
    os.makedirs(outdir, exist_ok=True)
    img_root = os.path.join(outdir, "images")
    log(f"站点 {site_code}，共 {len(asins)} 个 ASIN")

    sales_client = None
    if use_xiyou and xiyou.available():
        try:
            sales_client = xiyou.XiyouClient()
            log("西柚已连接，将尝试补充近 30 天销量")
        except Exception as e:
            log(f"西柚连接失败：{e}")

    items, failed = [], []
    with AmazonScraper(site_code) as scraper:
        for i, asin in enumerate(asins, 1):
            log(f"[{i}/{len(asins)}] 抓取 {asin}")
            try:
                item = {"asin": asin}
                items.append(_enrich(item, scraper, img_root, sales_client, site_code, log))
            except Exception as e:
                failed.append({"asin": asin, "error": str(e)[:160]})
                log(f"  {asin} 抓取失败：{str(e)[:80]}")
            time.sleep(random.uniform(2.5, 5))

    if sales_client:
        sales_client.__exit__(None, None, None)
    if not items:
        err = RuntimeError("所有 ASIN 都抓取失败，请检查站点或稍后重试")
        err.success_asins = []
        err.failed_asins = failed
        raise err

    note = f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　站点：{site_code}　ASIN 数：{len(items)}"
    if keywords:
        note += f"　关键词：{'、'.join(keywords) if isinstance(keywords, list) else keywords}"
    if category:
        note += f"　类目：{category}"
    request = {"asins": asins, "keywords": keywords or [], "category": category or ""}
    result = _finish(items, site_code, outdir, {"note": note}, log,
                     source="discovery" if keywords else "asin", request=request)
    result["success_asins"] = [item["asin"] for item in items]
    result["failed_asins"] = failed
    return result


def run_discovery_job(keywords, category, site_code, bsr_max, pages, outdir,
                      progress=None, use_xiyou=True, want=5):
    log = progress or (lambda m: None)
    get_site(site_code)
    os.makedirs(outdir, exist_ok=True)
    img_root = os.path.join(outdir, "images")
    log(f"站点 {site_code}｜关键词：{'、'.join(keywords)}｜BSR 上限：{bsr_max}")

    with AmazonScraper(site_code) as scraper:
        picked = discovery.discover(
            scraper, site_code, keywords, category=category, bsr_max=bsr_max,
            pages=pages, want=want, on_progress=log, use_xiyou=use_xiyou)
        if not picked:
            raise RuntimeError("没有找到符合 BSR 条件的竞品，请放宽 BSR 上限或换关键词")

        log(f"入选 {len(picked)} 个竞品，开始抓取详情")
        items, failed = [], []
        for i, c in enumerate(picked, 1):
            log(f"[{i}/{len(picked)}] {c['asin']}（BSR #{c.get('bsr_rank')}）")
            try:
                items.append(_enrich(c, scraper, img_root, None, site_code, log))
            except Exception as e:
                failed.append({"asin": c["asin"], "error": str(e)[:160]})
                log(f"  {c['asin']} 抓取失败：{str(e)[:80]}")

    if not items:
        err = RuntimeError("入选竞品全部抓取失败，请稍后重试")
        err.success_asins = []
        err.failed_asins = failed
        raise err

    note = (f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　站点：{site_code}　"
            f"关键词：{'、'.join(keywords)}　类目：{category or '自动聚类'}　BSR 上限：{bsr_max}")
    request = {"keywords": keywords, "category": category or "", "bsr_max": bsr_max,
               "pages": pages, "want": want}
    result = _finish(items, site_code, outdir, {"note": note}, log,
                     source="discovery", request=request)
    result["success_asins"] = [item["asin"] for item in items]
    result["failed_asins"] = failed
    return result
