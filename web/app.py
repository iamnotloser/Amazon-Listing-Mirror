"""竞品 LISTING 分析器 Web 服务。

启动：python web/app.py
默认地址：http://127.0.0.1:8010
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import collection_store, discovery, pipeline
from analysis import runner as analysis_runner, translate

app = FastAPI(title="竞品 LISTING 分析器")
STATIC = Path(__file__).resolve().parent / "static"
OUTPUT = ROOT / "output"
JOBS = {}
LOCK = threading.Lock()
Q = queue.Queue()


def _collection_dir(collection_id):
    """校验外部传入的资料 ID，并限制访问范围在 output 内。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", collection_id or ""):
        raise HTTPException(400, "资料 ID 无效")
    root = OUTPUT.resolve()
    path = (root / collection_id).resolve()
    if path.parent != root:
        raise HTTPException(400, "资料 ID 无效")
    return path


class Payload(BaseModel):
    mode: str = "asin"
    asins: str = ""
    site: str = "US"
    keywords: str = ""
    category: str = ""
    bsr_max: int = 100
    pages: int = 3
    use_xiyou: bool = True
    want: int = 5
    collection_id: str = ""
    modules: list | None = None


def _worker():
    while True:
        job_id, params = Q.get()
        with LOCK:
            job = JOBS.get(job_id)
            if job is None:
                Q.task_done()
                continue
            job["status"] = "running"
            job["logs"].append("开始执行")

        def progress(msg):
            with LOCK:
                cur = JOBS.get(job_id)
                if cur is not None:
                    cur["logs"].append(str(msg))
                    if len(cur["logs"]) > 500:
                        cur["logs"] = cur["logs"][-500:]

        try:
            outdir = str(OUTPUT / job_id)
            if params["mode"] == "analyze":
                cdir = _collection_dir(params["collection_id"])
                result = analysis_runner.run_analysis(cdir, params.get("modules") or None)
                with LOCK:
                    JOBS[job_id]["status"] = "done"
                    JOBS[job_id]["result"] = {
                        "collection_id": params["collection_id"],
                        "analyses": f"/api/collections/{params['collection_id']}/analyses",
                        "analysis_id": result.get("analysis_id"),
                    }
            else:
                if params["mode"] == "asin":
                    result = pipeline.run_asin_job(
                        params["asins"], params["site"], outdir,
                        progress=progress, use_xiyou=params["use_xiyou"])
                elif params.get("asins"):
                    result = pipeline.run_asin_job(
                        params["asins"], params["site"], outdir,
                        progress=progress, use_xiyou=params["use_xiyou"],
                        keywords=params.get("keywords", []),
                        category=params.get("category", ""))
                else:
                    result = pipeline.run_discovery_job(
                        params["keywords"], params["category"], params["site"],
                        params["bsr_max"], params["pages"], outdir,
                        progress=progress, use_xiyou=params["use_xiyou"],
                        want=params.get("want", 5))
                with LOCK:
                    JOBS[job_id]["status"] = "done"
                    JOBS[job_id]["result"] = {
                        "count": result["count"],
                        "xlsx": os.path.basename(result["xlsx"]),
                        "download": f"/api/download/{job_id}",
                        "collection_id": result["collection_id"],
                        "data": f"/api/collections/{result['collection_id']}",
                        "package": f"/api/collections/{result['collection_id']}/package",
                    }
        except Exception as e:
            with LOCK:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = f"{type(e).__name__}: {e}"
        finally:
            with LOCK:
                JOBS[job_id]["finished_at"] = time.time()
            Q.task_done()


threading.Thread(target=_worker, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.svg")
def favicon():
    p = STATIC / "favicon.svg"
    if p.exists():
        return FileResponse(p, media_type="image/svg+xml")
    raise HTTPException(404)


@app.get("/analysis")
def analysis_page():
    """独立评分页：?collection=xxx 载入指定采集结果。"""
    return FileResponse(STATIC / "analysis.html", headers={"Cache-Control": "no-store"})


@app.post("/api/jobs")
def create_job(p: Payload):
    if p.mode == "analyze":
        cdir = _collection_dir(p.collection_id or "")
        if not (cdir / "data.json").is_file():
            raise HTTPException(404, "该资料包没有结构化数据，请重新采集")
        job_id = uuid.uuid4().hex[:10]
        params = {"mode": "analyze", "collection_id": p.collection_id or "",
                  "modules": [m for m in (p.modules or ["title", "bullets", "images"]) if m]}
        with LOCK:
            JOBS[job_id] = {"status": "queued", "logs": ["已进入队列"], "result": None,
                            "error": None, "params": params, "created_at": time.time()}
        Q.put((job_id, params))
        return {"job_id": job_id}

    if p.mode == "asin":
        asins, bad = pipeline.parse_asins(p.asins)
        if not asins:
            raise HTTPException(400, "请填写至少一个有效 ASIN（B0 开头 10 位）")
        if bad:
            raise HTTPException(400, f"以下内容不是有效 ASIN：{', '.join(bad[:5])}")
        if len(asins) > 10:
            raise HTTPException(400, "单次最多 10 个 ASIN，请分批处理")
        params = {"mode": "asin", "asins": asins, "site": p.site,
                  "use_xiyou": p.use_xiyou}
    else:
        kws = [x.strip() for x in p.keywords.replace("，", ",").split(",") if x.strip()]
        if not kws:
            raise HTTPException(400, "请填写至少一个关键词")
        params = {"mode": "discover", "keywords": kws, "category": p.category.strip(),
                  "site": p.site, "bsr_max": max(1, p.bsr_max),
                  "pages": max(1, min(p.pages, 10)), "use_xiyou": p.use_xiyou,
                  "want": max(1, p.want)}
        if p.asins.strip():
            asins, bad = pipeline.parse_asins(p.asins)
            if bad:
                raise HTTPException(400, f"以下内容不是有效 ASIN：{', '.join(bad[:5])}")
            params["asins"] = asins
            if len(asins) > 10:
                raise HTTPException(400, "单次最多 10 个 ASIN，请分批处理")

    job_id = uuid.uuid4().hex[:10]
    with LOCK:
        JOBS[job_id] = {"status": "queued", "logs": ["已进入队列"], "result": None,
                        "error": None, "params": params, "created_at": time.time()}
    Q.put((job_id, params))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return {"status": job["status"], "logs": job["logs"],
                "result": job["result"], "error": job["error"]}


@app.get("/api/download/{job_id}")
def download(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(404, "结果不存在")
    path = OUTPUT / job_id / "打包下载.zip"
    if not path.exists():
        raise HTTPException(404, "打包文件不存在")
    return FileResponse(path, filename=f"竞品LISTING分析_{job_id}.zip",
                        media_type="application/zip")


@app.get("/api/collections/{collection_id}")
def get_collection(collection_id: str):
    """读取结构化采集结果；服务重启后仍可使用历史 collection_id。"""
    try:
        payload = collection_store.load_collection(_collection_dir(collection_id))
    except FileNotFoundError:
        raise HTTPException(404, "采集资料包不存在")
    except (ValueError, OSError) as e:
        raise HTTPException(500, f"采集资料包读取失败：{e}")
    return {**payload, "file_base_url": f"/api/collections/{collection_id}/files"}


@app.get("/api/collections/{collection_id}/package")
def download_collection(collection_id: str):
    path = _collection_dir(collection_id) / "打包下载.zip"
    if not path.is_file():
        raise HTTPException(404, "资料包尚未生成或不存在")
    return FileResponse(path, filename=f"竞品资料包_{collection_id}.zip",
                        media_type="application/zip")


@app.get("/api/collections/{collection_id}/files/{file_path:path}")
def get_collection_file(collection_id: str, file_path: str):
    """按 data.json 中的相对路径读取图片等文件。"""
    root = _collection_dir(collection_id)
    path = (root / file_path).resolve()
    if root not in path.parents:
        raise HTTPException(400, "文件路径无效")
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path)


class AnalysisPayload(BaseModel):
    """modules 留空表示三个模块全跑；各模块独立出分，不合成总分。"""
    modules: list[str] = []


def _check_analysis_id(analysis_id):
    if not re.fullmatch(r"an_[A-Za-z0-9_]{1,40}", analysis_id or ""):
        raise HTTPException(400, "分析 ID 无效")


@app.post("/api/collections/{collection_id}/analyses")
def create_analysis(collection_id: str, p: AnalysisPayload | None = None):
    cdir = _collection_dir(collection_id)
    if not (cdir / "data.json").is_file():
        raise HTTPException(404, "该资料包没有结构化数据，请重新采集")
    modules = (p.modules if p else None) or None
    try:
        return analysis_runner.run_analysis(cdir, modules)
    except ValueError as e:
        raise HTTPException(500, f"评分失败：{e}")


@app.get("/api/collections/{collection_id}/analyses")
def list_analyses(collection_id: str):
    cdir = _collection_dir(collection_id)
    if not cdir.is_dir():
        raise HTTPException(404, "资料包不存在")
    return {"collection_id": collection_id, "items": analysis_runner.list_analyses(cdir)}


@app.get("/api/collections/{collection_id}/analyses/{analysis_id}")
def get_analysis(collection_id: str, analysis_id: str):
    _check_analysis_id(analysis_id)
    cdir = _collection_dir(collection_id)
    try:
        return analysis_runner.load_analysis(cdir, analysis_id)
    except FileNotFoundError:
        raise HTTPException(404, "分析结果不存在")


class TranslatePayload(BaseModel):
    asin: str


@app.post("/api/collections/{collection_id}/translate")
def translate_listing(collection_id: str, p: TranslatePayload):
    """按 ASIN 翻译标题与五点；结果缓存在资料包内，重复请求不再调用模型。"""
    cdir = _collection_dir(collection_id)
    try:
        payload = collection_store.load_collection(cdir)
    except FileNotFoundError:
        raise HTTPException(404, "采集资料包不存在")
    asin = (p.asin or "").strip().upper()
    item = next((x for x in payload.get("items", []) if x.get("asin") == asin), None)
    if not item:
        raise HTTPException(404, "该资料包中没有这个 ASIN")
    try:
        return translate.translate_item(cdir, item)
    except RuntimeError as e:
        raise HTTPException(502, f"翻译失败：{e}")


class DiscoverPayload(BaseModel):
    keywords: str
    site: str = "US"
    pages: int = 3


@app.post("/api/discover")
def api_discover(p: DiscoverPayload):
    """轻量候选搜索：只抓搜索页，不走详情页 / BSR，几秒出结果。"""
    kws = [x.strip() for x in p.keywords.replace("，", ",").split(",") if x.strip()]
    if not kws:
        raise HTTPException(400, "请填写至少一个关键词")
    pages = max(1, min(p.pages, 10))
    from app.scraper import AmazonScraper
    try:
        with AmazonScraper(p.site) as scraper:
            candidates = discovery.discover_candidates(scraper, kws, pages=pages)
    except Exception as e:
        raise HTTPException(502, f"搜索失败：{e}")
    return {"count": len(candidates), "items": candidates, "keywords": kws, "site": p.site}


if __name__ == "__main__":
    # 启动前清理遗留的 Playwright chromium 进程，避免被上一次卡死的抓取残留
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe", "/IM", "chromium.exe"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    OUTPUT.mkdir(exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8010")), log_level="info")
