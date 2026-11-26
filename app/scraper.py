"""Playwright 抓取层：单品详情、搜索页、变体截图。

设计要点（沿用已跑通的反爬方案）：
- 抹掉 navigator.webdriver，注入 chrome 对象与语言
- 强制 i18n-prefs / lc-main cookie，避免出口 IP 导致货币、语言错乱
- 每次抓取独立 Page，失败重试；命中验证码抛异常，由上层记录
"""
import json
import random
import re
import time

from playwright.sync_api import sync_playwright

from .sites import get_site

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _clean(s):
    return re.sub(r"[\u200e\u200f\u00a0]", " ", s or "").strip()


def _split_title(title):
    """按逗号语义段拆分主标题与亮点；完整原文仍保留在 title 字段。"""
    title = _clean(title)
    if not title:
        return "", ""
    parts = [p.strip(" ,，;；") for p in re.split(r"\s*[,，]\s*", title) if p.strip(" ,，;；")]
    if len(parts) < 2:
        return title, ""
    main_count = 2 if len(parts) >= 4 else 1
    while main_count < len(parts) - 1 and len(", ".join(parts[:main_count])) < 28:
        main_count += 1
    return ", ".join(parts[:main_count]), ", ".join(parts[main_count:])


def _upgrade_img(url: str) -> str:
    """缩略图升级为高清主图。"""
    if not url:
        return url
    url = url.split(" ")[0]
    return re.sub(r"\._[^/]+_\.(jpg|png|jpeg|webp)$", r"._AC_SL1500_.\1", url)


def _parse_rank(text: str) -> list:
    """从 Best Sellers Rank 文本中解析各类目排名。"""
    out = []
    for m in re.finditer(r"#([\d,]+)\s+in\s+([^#(]+)", text or ""):
        rank = int(m.group(1).replace(",", ""))
        cat = _clean(m.group(2))
        cat = re.split(r"\s*\(See|\s*\(Top", cat)[0].strip(" ,")
        if cat:
            out.append({"category": cat, "rank": rank})
    return out


def _parse_variations(text: str) -> list:
    """从 twister 文本解析维度，如 Size: 9.25in / Color: Black。"""
    dims = []
    cur = None
    for raw in (text or "").splitlines():
        line = _clean(raw)
        if not line or "$" in line or "savings" in line.lower() or line.lower().startswith("make a selection"):
            if cur is not None and line and "$" not in line and "savings" not in line.lower():
                pass
            continue
        m = re.match(r"^([A-Za-z][A-Za-z /&]{1,28}):\s*(.*)$", line)
        if m:
            cur = {"name": m.group(1).strip(), "values": []}
            dims.append(cur)
            val = m.group(2).strip()
            if val and "$" not in val and len(val) < 60:
                cur["values"].append(val)
            continue
        if cur is not None and len(line) < 60:
            cur["values"].append(line)
    for d in dims:
        d["values"] = list(dict.fromkeys(d["values"]))
    return [d for d in dims if d["values"]]


class AmazonScraper:
    def __init__(self, site_code="US", headless=True):
        self.site = get_site(site_code)
        self.headless = headless
        self._pw = None
        self._browser = None
        self._ctx = None
        self._warm = False

    # ---------- 生命周期 ----------
    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._ctx = self._browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 1000},
            locale=self.site["locale"],
            timezone_id=self.site["tz"],
            extra_http_headers={
                "Accept-Language": f"{self.site['locale']},en;q=0.9",
                "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-mobile": "?0",
            },
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
            "Object.defineProperty(navigator,'languages',{get:()=>['%s','en']});" % self.site["locale"]
        )
        host = self.site["domain"].replace("https://", "")
        try:
            self._ctx.add_cookies([
                {"name": "i18n-prefs", "value": self.site["currency"], "domain": f".{host}", "path": "/"},
                {"name": "lc-main", "value": self.site["locale"].replace("-", "_"), "domain": f".{host}", "path": "/"},
            ])
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _warmup(self):
        if self._warm:
            return
        page = self._ctx.new_page()
        try:
            page.goto(self.site["domain"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(random.randint(800, 1600))
        except Exception:
            pass
        finally:
            page.close()
        self._warm = True

    @staticmethod
    def _is_blocked(body: str) -> bool:
        return ("Enter the characters you see below" in body
                or "not a robot" in body
                or "api-services-support@amazon.com" in body)

    # ---------- 单品详情 ----------
    FETCH_ATTEMPTS = 10

    def fetch(self, asin: str, with_images=True, variation_shot=None,
              retries=None, log=None) -> dict:
        """默认最多尝试 10 次，全部失败才抛错；间隔递增但封顶 10 秒。"""
        tries = self.FETCH_ATTEMPTS - 1 if retries is None else retries
        last_err = None
        for attempt in range(tries + 1):
            try:
                return self._fetch_once(asin, with_images, variation_shot)
            except Exception as e:
                last_err = e
                if attempt < tries:
                    wait = min(2 + attempt * 2, 10)
                    if log:
                        log(f"  {asin}: 第 {attempt + 1}/{tries} 次失败（{str(e)[:60]}），{wait}s 后重试")
                    time.sleep(wait)
        if log:
            log(f"  {asin}: 已重试 {tries + 1} 次仍失败，放弃")
        raise last_err

    def _fetch_once(self, asin, with_images, variation_shot):
        self._warmup()
        url = f"{self.site['domain']}/dp/{asin}"
        page = self._ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(1200, 2200))
            body = page.inner_text("body")[:1200]
            if self._is_blocked(body):
                raise RuntimeError(f"命中验证码: {asin}")
            if not page.query_selector("#productTitle"):
                page.wait_for_timeout(2500)

            # 详情表、BSR、A+ 模块都是随滚动懒加载，需要滚到页面底部
            for _ in range(20):
                page.evaluate("window.scrollBy(0, 1200)")
                page.wait_for_timeout(220)
            page.wait_for_timeout(1500)

            data = {"asin": asin, "url": url, "http_status": resp.status if resp else None}

            def txt(sel):
                el = page.query_selector(sel)
                return _clean(el.inner_text()) if el else None

            data["title"] = txt("#productTitle")
            data["title_main"], data["title_highlights"] = _split_title(data["title"])
            raw_brand = txt("#bylineInfo") or ""
            raw_brand = re.sub(r"^(Visit the|Brand:)\s*", "", raw_brand)
            raw_brand = re.sub(r"\s*Store$", "", raw_brand)
            raw_brand = re.sub(r"^by\s+", "", raw_brand)
            data["brand"] = raw_brand or None

            data["price"] = (txt("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen")
                             or txt("#corePrice_feature_div .a-offscreen")
                             or txt(".a-price .a-offscreen")
                             or txt("#priceblock_ourprice"))
            data["list_price"] = (txt("#basisPrice .a-offscreen")
                                  or txt(".basisPrice .a-offscreen")
                                  or txt("#listPrice")
                                  or txt("#priceblock_listprice"))
            data["typical_price"] = txt("#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen")
            data["coupon"] = txt("#couponBadge") or txt("#promoPriceBlockMessage")

            acr = page.query_selector("#acrPopover")
            data["rating"] = _clean(acr.get_attribute("title")) if acr else None
            data["review_count"] = txt("#acrCustomerReviewText")

            data["bullets"] = [
                _clean(t) for t in page.eval_on_selector_all(
                    "#feature-bullets li span.a-list-item", "els => els.map(e => e.innerText)")
                if len(_clean(t)) > 8
            ][:10]

            data["breadcrumbs"] = page.eval_on_selector_all(
                "#wayfinding-breadcrumbs_feature_div ul li a", "els => els.map(e => e.innerText.trim())")

            # 规格 + 开售日 + BSR 原文
            specs = {}
            for sel in ["#productOverview_feature_div tr",
                        "#productDetails_feature_div tr",
                        "#productDetails_techSpec_section_1 tr",
                        "#productDetails_detailBullets_sections1 tr",
                        "table.a-keyvalue.prodDetTable tr"]:
                for row in page.eval_on_selector_all(sel, "els => els.map(e => e.innerText)"):
                    parts = [p.strip() for p in re.split(r"\s{2,}", _clean(row)) if p.strip()]
                    if len(parts) >= 2 and len(parts[0]) < 60:
                        if "var " in parts[1] or "P.when" in parts[1]:
                            continue
                        specs[parts[0]] = " ".join(parts[1:])[:250]
            detail_texts = [_clean(t) for t in page.eval_on_selector_all(
                "#detailBullets_feature_div li", "els => els.map(e => e.innerText)")]
            for t in detail_texts:
                if ":" in t and len(t) < 300:
                    k, v = t.split(":", 1)
                    k, v = _clean(k), _clean(v)
                    if k and k not in specs:
                        specs[k] = v[:250]
            data["specs"] = specs

            def spec_by(*keys):
                for k, v in specs.items():
                    kl = k.lower()
                    if any(x in kl for x in keys):
                        return v
                return None

            data["date_first_available"] = spec_by("date first available", "首次上架", "発売日", "erhältlich")
            data["manufacturer"] = spec_by("manufacturer", "brand name")
            data["model_number"] = spec_by("model number", "modellnummer", "型号")

            bsr_text = specs.get("Best Sellers Rank") or ""
            if not bsr_text:
                for k, v in specs.items():
                    if "sellers rank" in k.lower() or "bestseller-rang" in k.lower():
                        bsr_text = v
                        break
            if not bsr_text:
                for row in page.eval_on_selector_all(
                        "#productDetails_feature_div tr, table.a-keyvalue tr", "els => els.map(e => e.innerText)"):
                    if "Best Sellers Rank" in row:
                        bsr_text = _clean(row).replace("Best Sellers Rank", "")
                        break
            data["bsr_raw"] = bsr_text
            data["bsr"] = _parse_rank(bsr_text)

            data["ac_badge"] = bool(page.query_selector("#acBadge_feature_div")
                                    or page.query_selector(".ac-badge-wrapper")
                                    or page.query_selector('[data-csa-c-content-id*="ac-badge"]'))
            data["climate_badge"] = bool(
                page.query_selector("#climatePledgeFriendly")
                or page.query_selector('[data-csa-c-content-id*="climate"]')
                or "Climate Pledge Friendly" in body
            )

            # 变体：维度名 + 取值 + 数量（新旧两套 twister DOM 都要兼容）
            variations = {"dimensions": [], "count": 0, "text": ""}
            try:
                tw = page.query_selector("#twister_feature_div") or page.query_selector("#twister")
                if tw:
                    variations["text"] = _clean(tw.inner_text())[:1200]
                    rows = page.eval_on_selector_all(
                        "#twister_feature_div [id^=inline-twister-row]",
                        """els => els.map(e => ({
                            id: e.id,
                            values: [...e.querySelectorAll('li')].map(li =>
                                (li.querySelector('img')?.alt || (li.innerText || '').split('\\n')[0]).trim()
                            ).filter(Boolean)
                        }))""")
                    dims = []
                    for r in rows:
                        key = r["id"].replace("inline-twister-row-", "").replace("_name", "")
                        name = key.replace("_", " ").title()
                        vals = list(dict.fromkeys(r["values"]))
                        if vals:
                            dims.append({"name": name, "values": vals[:40]})
                    variations["dimensions"] = dims or _parse_variations(variations["text"])
                    variations["count"] = sum(len(d["values"]) for d in variations["dimensions"])
                    if variation_shot:
                        try:
                            tw.screenshot(path=variation_shot)
                            variations["screenshot"] = variation_shot
                        except Exception:
                            pass
            except Exception:
                pass
            data["variations"] = variations

            # 捆绑 / 配件
            bundle = []
            for sel in ["#sims_fbt .a-link-normal", "#bundleV2_feature_div .a-link-normal"]:
                try:
                    bundle += [_clean(t) for t in page.eval_on_selector_all(
                        sel, "els => els.map(e => e.innerText)") if _clean(t)]
                except Exception:
                    pass
            data["bundle_hint"] = list(dict.fromkeys(bundle))[:6]

            if with_images:
                data["images"] = self._collect_main_images(page)
                data["aplus_images"] = self._collect_aplus(page)
            if not data.get("title") or (not data.get("bullets") and not data.get("price")):
                raise RuntimeError(f"页面内容不完整，触发重试: {asin}")
            data["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return data
        finally:
            page.close()

    # ---------- 图片 ----------
    @staticmethod
    def _collect_main_images(page) -> list:
        urls = []
        try:
            dyn = page.eval_on_selector("#landingImage", "el => el ? el.getAttribute('data-a-dynamic-image') : null")
            if dyn:
                try:
                    urls += list(json.loads(dyn).keys())
                except Exception:
                    pass
            landing = page.eval_on_selector("#landingImage", "el => el ? (el.currentSrc || el.src) : null")
            if landing:
                urls.insert(0, landing)
            thumbs = page.eval_on_selector_all("#altImages img", "els => els.map(e => e.src)")
            for t in thumbs:
                if t and "sprite" not in t and "play-icon" not in t and "play-button" not in t:
                    urls.append(t)
        except Exception:
            pass
        out, seen = [], set()
        for u in urls:
            if not u:
                continue
            hi = _upgrade_img(u)
            key = hi.split("._")[0]
            if key in seen:
                continue
            seen.add(key)
            out.append(hi)
        return out[:9]

    @staticmethod
    def _collect_aplus(page) -> list:
        """提取 A+ 图。

        A+ 模块是分段懒加载的，DOM 里只会 hydrate 出一部分，直接读原始 HTML 最稳。
        #aplus 里还混着品牌故事的 166x182 商品小卡和 315x145 缩略图，按尺寸标记过滤。
        """
        try:
            html = page.content()
        except Exception:
            return []
        urls = re.findall(
            r"https?://m\.media-amazon\.com/images/S/aplus-[A-Za-z0-9_\-./,%]+", html)
        out, seen = [], set()
        for u in urls:
            u = u.rstrip(".,\"'")
            if "transparent-pixel" in u or "data:image" in u:
                continue
            # 品牌故事缩略图 / 商品小卡
            if "__AC_SR" in u or "SX315" in u or "SR166" in u or "SX166" in u:
                continue
            m = re.search(r"SX(\d+)", u)
            if m and int(m.group(1)) < 400:
                continue
            key = u.split("/")[-1].split(".")[0]
            if key in seen:
                continue
            seen.add(key)
            out.append(u)
        return out[:12]

    # ---------- 搜索页 ----------
    def search(self, keyword: str, pages: int = 5, limit: int = 60) -> list:
        self._warmup()
        out, seen = [], set()
        page = self._ctx.new_page()
        try:
            for p in range(1, max(1, pages) + 1):
                url = f"{self.site['domain']}/s?k={keyword.replace(' ', '+')}&page={p}"
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(random.randint(1200, 2200))
                if self._is_blocked(page.inner_text("body")[:1200]):
                    raise RuntimeError(f"搜索页命中验证码: {keyword}")
                cards = page.eval_on_selector_all(
                    '[data-component-type="s-search-result"]',
                    """els => els.map(e => ({
                        asin: e.getAttribute('data-asin'),
                        sponsored: !!e.querySelector('.puis-sponsored-label-text, [aria-label="Sponsored"]'),
                        title: e.querySelector('h2')?.innerText.trim() || '',
                        price: e.querySelector('.a-price .a-offscreen')?.innerText || '',
                        rating: e.querySelector('.a-icon-alt')?.innerText || '',
                        reviews: e.querySelector('[aria-label$="ratings"], .s-underline-text')?.innerText || '',
                        thumb: e.querySelector('img.s-image')?.getAttribute('src') || ''
                    }))""")
                for c in cards:
                    if not c["asin"] or c["sponsored"] or not c["title"]:
                        continue
                    if c["asin"] in seen:
                        continue
                    seen.add(c["asin"])
                    c["keyword"] = keyword
                    c["page"] = p
                    c["position"] = len(out) + 1
                    out.append(c)
                    if len(out) >= limit:
                        return out
            return out
        finally:
            page.close()
