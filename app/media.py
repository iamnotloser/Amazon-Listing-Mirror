"""图片下载：把抓到的 Amazon 图片存到本地，供 Excel 嵌入与打包下载。"""
import os
import re
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _ext(url: str) -> str:
    m = re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", url.lower())
    return m.group(1) if m else "jpg"


def download_image(url: str, path: str, timeout=25) -> bool:
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.amazon.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 800:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def download_set(urls, folder, prefix, limit=None):
    """按顺序下载，返回本地路径列表（跳过失败项）。"""
    out = []
    for i, u in enumerate(urls[:limit] if limit else urls, 1):
        p = os.path.join(folder, f"{prefix}_{i}.{_ext(u)}")
        if download_image(u, p):
            out.append(p)
    return out
