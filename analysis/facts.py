"""事实卡：只陈列客观数据，不参与任何评分。"""
import re


def _num(value):
    """从 '$11.89'、'11,89 €'、'$1,234.56' 之类的文本中取出数值。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    keep = "".join(ch for ch in str(value).replace(" ", "") if ch.isdigit() or ch in ".,")
    if not keep:
        return None
    if "," in keep and "." in keep:
        keep = keep.replace(",", "")
    elif "," in keep:
        keep = keep.replace(",", ".") if re.search(r",\d{2}$", keep) else keep.replace(",", "")
    try:
        return round(float(keep), 2)
    except ValueError:
        return None


def _reviews(value):
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def extract_facts(item):
    price = _num(item.get("price"))
    list_price = _num(item.get("list_price"))
    typical = _num(item.get("typical_price"))
    discount = None
    if price and list_price and list_price > price:
        discount = round(1 - price / list_price, 3)
    bsr = sorted(item.get("bsr") or [], key=lambda x: x.get("rank") or 10 ** 9)
    variations = item.get("variations") or {}
    return {
        "brand": item.get("brand"),
        "price": price,
        "list_price": list_price,
        "typical_price": typical,
        "discount": discount,
        "coupon": item.get("coupon"),
        "rating": item.get("rating"),
        "reviews": _reviews(item.get("review_count")),
        "bsr_top": bsr[0] if bsr else None,
        "bsr": bsr[:3],
        "sales_30d": item.get("sales_30d"),
        "date_first_available": item.get("date_first_available"),
        "variations": variations.get("count") or 0,
        "ac_badge": bool(item.get("ac_badge")),
        "climate_badge": bool(item.get("climate_badge")),
        "category_path": item.get("breadcrumbs") or [],
    }


def collect_gaps(item, facts):
    """缺失数据只记录，不转化为扣分。"""
    gaps = []
    if facts["sales_30d"] in (None, ""):
        gaps.append("近30天销量缺失")
    if not facts["date_first_available"]:
        gaps.append("开售日缺失")
    if facts["rating"] is None:
        gaps.append("评分缺失")
    if not facts["bsr"]:
        gaps.append("BSR 缺失")
    # 资料包落盘时已把图片 URL 归一化到 images / source_image_urls，aplus_images 不再保留
    images = item.get("images") or {}
    source = item.get("source_image_urls") or {}
    if not (images.get("aplus") or source.get("aplus")):
        gaps.append("A+ 图缺失")
    return gaps
