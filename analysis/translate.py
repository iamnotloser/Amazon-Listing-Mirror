"""标题 / 五点的中译：按 ASIN 缓存到资料包，按需翻译，走 OpenAI 兼容文本模型。"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_NAME = "translations.json"

PROMPT = (
    "你是跨境电商本地化译员。把 items 里的每一条英文翻译成简体中文："
    "保留品牌名、型号、数字与尺寸单位；不要增删信息，不要合并或拆分条目；"
    "五点每条仍对应一条。严格返回 JSON：{\"items\": [...]}，"
    "数量与顺序必须与输入完全一致，不要输出任何解释。"
)


def available():
    return bool(os.getenv("TEXT_API_KEY") or os.getenv("VISION_API_KEY"))


def _chat(texts):
    base = (os.getenv("TEXT_BASE_URL") or os.getenv("VISION_BASE_URL")
            or "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("TEXT_MODEL") or "qwen-plus"
    key = os.getenv("TEXT_API_KEY") or os.getenv("VISION_API_KEY")
    if not key:
        raise RuntimeError("未配置文本模型密钥（TEXT_API_KEY 或 VISION_API_KEY）")

    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user",
                      "content": PROMPT + "\n\n" + json.dumps({"items": texts}, ensure_ascii=False)}],
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode())
        text = payload["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise RuntimeError("模型未返回 JSON")
        items = json.loads(match.group(0)).get("items") or []
        if len(items) != len(texts):
            raise RuntimeError(f"翻译条数不一致（{len(items)}/{len(texts)}）")
        return [str(x).strip() for x in items]
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "ignore")[:160]
        except Exception:
            detail = ""
        raise RuntimeError(f"HTTP {e.code} {detail}".strip()[:220])


def _load_cache(cdir):
    path = Path(cdir) / CACHE_NAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cdir, cache):
    path = Path(cdir) / CACHE_NAME
    tmp = path.with_name(CACHE_NAME + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def translate_item(cdir, item):
    """翻译单个 ASIN 的标题与五点；命中缓存直接返回。"""
    asin = item.get("asin") or ""
    cache = _load_cache(cdir)
    if asin in cache:
        return cache[asin]

    title = item.get("title") or ""
    main = item.get("title_main") or ""
    highlights = item.get("title_highlights") or ""
    bullets = [b for b in (item.get("bullets") or []) if b]

    zh = _chat([title, main, highlights] + bullets)
    result = {
        "asin": asin,
        "title_zh": zh[0],
        "title_main_zh": zh[1],
        "title_highlights_zh": zh[2],
        "bullets_zh": zh[3:],
        "model": os.getenv("TEXT_MODEL") or "qwen-plus",
        "translated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache[asin] = result
    _save_cache(cdir, cache)
    return result
