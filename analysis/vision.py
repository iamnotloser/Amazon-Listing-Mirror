"""可选的多模态图片评审：未配置密钥时直接跳过，不影响规则分。"""
import base64
import json
import os
import re
import urllib.error
import urllib.request
from io import BytesIO

from PIL import Image as PILImage

from .common import sub

# 单次请求送图上限。qwen3-vl-plus 等模型支持更多（百炼官方：最多 2000 张 / 请求体 ≤1GB），
# GLM-4V-flash 则只支持 5 张。超过此值时 review() 会自动分批调用再聚合，因此不会漏看图片。
# 可用 VISION_MAX_IMAGES 覆盖（换只支持 5 张的模型时设为 5）。
MAX_IMAGES_DEFAULT = 20

PROMPT = (
    "你是亚马逊 Listing 图片审核员。请只依据提供的图片，输出严格 JSON，不要任何解释文字。"
    "字段：scene_quality(0-10 场景/生活方式图是否清晰传达使用场景)、"
    "detail_coverage(0-10 是否覆盖细节、尺寸、材质、对比)、"
    "aplus_design(0-10 A+ 图文信息设计是否完整；图片会标明主图与 A+ 图，若没有 A+ 图则给 0，不要用主图猜测)、"
    "text_risk(0-10 仅评估商品主图是否干净无违规文字水印，A+ 图不计入；10 表示完全干净)、"
    "observations(3-6 条中文短句，指出具体问题或优点)。"
)


def _encode(path, max_side=800, quality=80):
    with PILImage.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def available():
    return bool(os.getenv("VISION_API_KEY"))


def _request(picked, main_count):
    """对一批图片做一次视觉模型调用，返回单次结果结构。"""
    if not picked:
        return {"status": "skipped", "score": None, "reason": "没有可评审的图片"}
    base = os.getenv("VISION_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("VISION_MODEL", "gpt-4o-mini")
    aplus_count = len(picked) - main_count
    content = [{"type": "text", "text": PROMPT}]
    if aplus_count:
        content.append({"type": "text", "text":
                        f"本次共 {len(picked)} 张图：前 {main_count} 张为商品主图，后 {aplus_count} 张为 A+ 详情图。"})
    else:
        content.append({"type": "text", "text":
                        f"本次共 {len(picked)} 张图，全部为商品主图，没有 A+ 图。"})
    for p in picked:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + _encode(p)}})
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + os.environ["VISION_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode())
        text = payload["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {"status": "failed", "score": None, "reason": "模型未返回 JSON"}
        data = json.loads(match.group(0))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "ignore")[:160]
        except Exception:
            detail = ""
        return {"status": "failed", "score": None,
                "reason": f"调用失败：HTTP {e.code} {detail}".strip()[:220]}
    except Exception as e:
        return {"status": "failed", "score": None, "reason": f"调用失败：{str(e)[:120]}"}

    def num(key):
        try:
            return max(0.0, min(10.0, float(data.get(key) or 0)))
        except (TypeError, ValueError):
            return 0.0

    subs = [
        sub("scene", "场景表达", 25, num("scene_quality") * 2.5, {"raw": num("scene_quality")}, "多模态评分"),
        sub("detail", "细节覆盖", 25, num("detail_coverage") * 2.5, {"raw": num("detail_coverage")}, "多模态评分"),
        sub("aplus_design", "A+ 信息设计", 25, num("aplus_design") * 2.5, {"raw": num("aplus_design")}, "多模态评分"),
        sub("text_risk", "主图纯净度", 25, num("text_risk") * 2.5, {"raw": num("text_risk")}, "多模态评分"),
    ]
    return {"status": "ok", "score": round(sum(s["score"] for s in subs), 1),
            "subs": subs, "observations": [str(x) for x in (data.get("observations") or [])][:6],
            "model": model}


def review(paths, limit=None, main_count=None):
    """视觉语义评审：看完所有图片。超过单次上限时自动分批调用，再聚合成一次结果。"""
    if not available():
        return {"status": "skipped", "score": None,
                "reason": "未配置 VISION_API_KEY，跳过语义评审"}
    if not paths:
        return {"status": "skipped", "score": None, "reason": "没有可评审的图片"}

    limit = limit or int(os.getenv("VISION_MAX_IMAGES") or MAX_IMAGES_DEFAULT)
    if limit < 1:
        limit = 1
    total = len(paths)
    main_count = total if main_count is None else max(0, min(main_count, total))

    # 分批：每批最多 limit 张，保持主图在前的顺序，并计算每批内主图数量用于标注
    batches = []
    for start in range(0, total, limit):
        chunk = paths[start:start + limit]
        chunk_main = sum(1 for i in range(start, start + len(chunk)) if i < main_count)
        batches.append((chunk, chunk_main))

    results = [_request(chunk, cm) for chunk, cm in batches]
    ok = [r for r in results if r.get("status") == "ok"]
    if not ok:
        return results[0]

    # 聚合：4 个子项取各成功批次的平均，observations 合并去重后截断
    ids = ["scene", "detail", "aplus_design", "text_risk"]
    agg = {}
    for sid in ids:
        vals = []
        for r in ok:
            for s in r.get("subs") or []:
                if s.get("id") == sid:
                    vals.append(s.get("score") or 0)
        agg[sid] = round(sum(vals) / len(vals), 1) if vals else 0.0

    obs, seen = [], set()
    for r in ok:
        for o in r.get("observations") or []:
            if o and o not in seen:
                seen.add(o)
                obs.append(o)
    subs = [
        sub("scene", "场景表达", 25, agg["scene"], {"batches": len(ok)}, "多模态评分"),
        sub("detail", "细节覆盖", 25, agg["detail"], {"batches": len(ok)}, "多模态评分"),
        sub("aplus_design", "A+ 信息设计", 25, agg["aplus_design"], {"batches": len(ok)}, "多模态评分"),
        sub("text_risk", "主图纯净度", 25, agg["text_risk"], {"batches": len(ok)}, "多模态评分"),
    ]
    return {"status": "ok", "score": round(sum(s["score"] for s in subs), 1),
            "subs": subs, "observations": obs[:6], "model": ok[0].get("model"),
            "batches": len(ok), "images": total,
            "failed_batches": len(results) - len(ok)}
