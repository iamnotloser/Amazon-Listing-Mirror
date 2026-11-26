"""采集资料包存储：统一落盘结构化数据，并提供可重复读取的入口。"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"
DATA_FILE = "data.json"
MANIFEST_FILE = "manifest.json"


def _write_json(path, payload):
    """先写临时文件再替换，避免下游读到写了一半的 JSON。"""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)


def _relative_path(path, root):
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def _normalize_item(item, root):
    """保留采集字段，将本机绝对图片路径转换为资料包内相对路径。"""
    raw_main = item.get("images") or []
    raw_aplus = item.get("aplus_images") or []
    excluded = {"images", "aplus_images", "main_local", "aplus_local", "variation_local"}
    data = {k: v for k, v in item.items() if not k.startswith("_") and k not in excluded}
    data["source_image_urls"] = {"main": raw_main, "aplus": raw_aplus}
    data["images"] = {
        "main": [p for p in (_relative_path(x, root) for x in (item.get("main_local") or [])) if p],
        "aplus": [p for p in (_relative_path(x, root) for x in (item.get("aplus_local") or [])) if p],
        "variation": _relative_path(item.get("variation_local"), root),
    }
    return data


def save_collection(outdir, items, site_code, source, request=None):
    """保存一次采集的标准资料包；manifest 最后写入，作为完成标志。"""
    root = Path(outdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    collection_id = root.name
    normalized = [_normalize_item(item, root) for item in items]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "collection_id": collection_id,
        "site": site_code,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request": request or {},
        "items": normalized,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "collection_id": collection_id,
        "status": "done",
        "site": site_code,
        "source": source,
        "created_at": payload["created_at"],
        "item_count": len(normalized),
        "data_file": DATA_FILE,
        "image_root": "images",
    }
    data_path = root / DATA_FILE
    manifest_path = root / MANIFEST_FILE
    _write_json(data_path, payload)
    _write_json(manifest_path, manifest)
    return {"collection_id": collection_id, "data": str(data_path),
            "manifest": str(manifest_path), "count": len(normalized)}


def load_collection(outdir):
    """从磁盘读取采集结果，不依赖服务进程内的任务状态。"""
    root = Path(outdir).resolve()
    with open(root / DATA_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload.get("items"), list) or not payload.get("collection_id"):
        raise ValueError("采集资料包格式无效")
    return payload
