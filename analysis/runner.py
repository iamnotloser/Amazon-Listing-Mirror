"""评分入口：逐 ASIN 跑指定模块，只输出分项结果，不合成链接总分。"""
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from . import vision
from .bullets_eval import evaluate_bullets
from .facts import collect_gaps, extract_facts
from .images_eval import evaluate_images
from .title_eval import evaluate_title

SCHEMA_VERSION = "1.0"
ALLOWED = ("title", "bullets", "images")
DATA_FILE = "data.json"
ANALYSIS_DIR = "analysis"


def _read_collection(collection_dir):
    path = Path(collection_dir) / DATA_FILE
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload.get("items"), list):
        raise ValueError("采集资料包缺少 items 字段")
    return payload


def _write_json(path, payload):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)


def _normalize_modules(modules):
    picked = [m for m in (modules or ALLOWED) if m in ALLOWED]
    return picked or list(ALLOWED)


def run_analysis(collection_dir, modules=None):
    data = _read_collection(collection_dir)
    modules = _normalize_modules(modules)
    analysis_id = "an_" + time.strftime("%Y%m%d_%H%M%S") + "_" + f"{random.randint(0, 0xFFFF):04x}"

    items, gap_counter = [], {}
    for item in data["items"]:
        facts = extract_facts(item)
        entry = {"asin": item.get("asin"), "facts": facts}
        gaps = collect_gaps(item, facts)

        if "title" in modules:
            entry["title"] = evaluate_title(item)
        if "bullets" in modules:
            entry["bullets"] = evaluate_bullets(item)
        if "images" in modules:
            result = evaluate_images(item, collection_dir)
            base = Path(collection_dir)
            images = item.get("images") or {}
            main_paths, aplus_paths = [], []
            for rel in (images.get("main") or [])[:3]:
                p = base / rel
                if p.is_file():
                    main_paths.append(str(p))
            for rel in (images.get("aplus") or [])[:3]:
                p = base / rel
                if p.is_file():
                    aplus_paths.append(str(p))
            # 标明主图与 A+ 图，避免模型把主图误判成 A+ 设计
            review = vision.review(main_paths + aplus_paths, main_count=len(main_paths))
            result["vision"] = review
            gaps += result.get("data_gaps") or []
            if review.get("status") == "skipped":
                gaps.append("图片语义评分未启用")
            entry["images"] = result

        entry["data_gaps"] = sorted(set(gaps))
        for g in entry["data_gaps"]:
            gap_counter[g] = gap_counter.get(g, 0) + 1
        items.append(entry)

    report = {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": analysis_id,
        "collection_id": data.get("collection_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "modules": modules,
        "aggregation": "none",
        "note": "各模块独立打分，不合成链接总分；客观数据见 facts，不参与评分",
        "summary": {"item_count": len(items), "data_gaps": gap_counter},
        "items": items,
    }

    out_dir = Path(collection_dir) / ANALYSIS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / f"{analysis_id}.json", report)
    return report


def list_analyses(collection_dir):
    out_dir = Path(collection_dir) / ANALYSIS_DIR
    if not out_dir.is_dir():
        return []
    rows = []
    for path in sorted(out_dir.glob("*.json"), reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            continue
        rows.append({
            "analysis_id": payload.get("analysis_id") or path.stem,
            "created_at": payload.get("created_at"),
            "modules": payload.get("modules"),
            "item_count": len(payload.get("items") or []),
        })
    return rows


def load_analysis(collection_dir, analysis_id):
    path = Path(collection_dir) / ANALYSIS_DIR / f"{analysis_id}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
