# Amazon Listing Mirror

亚马逊竞品 Listing 采集与评分工具。按 **ASIN** 或 **关键词** 找到竞品，抓取主图与 A+ 图，生成带图 Excel 与结构化资料包；再对 **标题 / 五点 / 图片** 三个模块分别打分，可选接入视觉模型做语义评审，并提供中英对照的网页对照视图。

采集与分析解耦：Excel 给人看，`data.json` 给评分模块读，压缩包里只放 Excel 和图片，不暴露服务端脚本。

## 功能

- **两种采集模式**
  - 按 ASIN：批量输入 ASIN，抓取标题、五点、价格、评分、BSR、规格、主图、A+ 图与变体截图。
  - 按关键词：搜索页取候选，西柚 MCP 或详情页校验类目 BSR，去重取 BSR Top 5。
- **Excel 模板输出**：标题与亮点分列、图片贴合单元格、超 6 个 ASIN 自动拆表、多站点按洲分组。
- **反爬与重试**：Playwright 抹除自动化特征、注入语言与货币 Cookie；单品抓取默认最多尝试 10 次，间隔递增封顶 10 秒。
- **资料存储层**：每个采集批次落盘为独立资料包，服务重启后仍可按 `collection_id` 读取与评分。
- **独立评分页**：每个 ASIN 一行横向对照，左侧主图缩略图，右侧标题 / 五点 / 图片三列分数；展开可看子项、问题建议与事实卡。
- **不合成总分**：三个模块各自 0–100 分，价格、评分、BSR、销量只做事实卡。
- **图片语义评审（可选）**：接入兼容 OpenAI 协议的视觉模型，额外输出场景表达、细节覆盖、A+ 信息设计、主图纯净度四个子项。
- **中英对照**：标题与五点自动翻译为简体中文，列表行显示中文标题，细看弹窗左右两栏对照。

## 工作流

```
ASIN / 关键词
   │
   ├─ app/scraper.py        抓详情、主图、A+ 图、变体
   ├─ app/discovery.py      关键词候选 + BSR 校验 + 去重
   ├─ app/pipeline.py       编排、重试、落盘
   ├─ app/excel_writer.py   生成带图 Excel
   └─ app/collection_store.py
            │
            ▼
   output/<collection_id>/
     ├─ data.json           结构化资料（服务端）
     ├─ manifest.json
     ├─ images/<ASIN>/      主图、A+ 图、变体截图
     ├─ 竞品LISTING分析_*.xlsx
     └─ 打包下载.zip         仅 Excel + images
            │
            ▼
   analysis/                标题 / 五点 / 图片独立评分
     ├─ title_eval.py
     ├─ bullets_eval.py
     ├─ images_eval.py
     ├─ vision.py           视觉模型语义评审（可选）
     ├─ translate.py        中英对照（按 ASIN 缓存）
     └─ runner.py
            │
            ▼
   web/static/analysis.html 横向对照页
```

## 快速开始

要求 Python 3.11+。

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium

cp .env.example .env      # 填写密钥后保存
python web/app.py         # http://127.0.0.1:8010
```

## 配置

所有配置通过项目根目录 `.env` 读取，`.env` 已被 `.gitignore` 忽略，不会提交。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `XIYOU_MCP_URL` / `XIYOU_MCP_TOKEN` | 否 | 西柚 MCP：补近 30 天销量、用西柚校验类目 BSR |
| `VISION_BASE_URL` / `VISION_MODEL` / `VISION_API_KEY` | 否 | 图片语义评审，需兼容 OpenAI `/chat/completions` |
| `VISION_MAX_IMAGES` | 否 | 单次语义评审最多送图数，默认 5 |
| `TEXT_MODEL` | 否 | 标题 / 五点中译模型，默认 `qwen-plus`，复用视觉模型的 Base URL 与 Key |
| `TEXT_BASE_URL` / `TEXT_API_KEY` | 否 | 中译单独使用其他服务商时填写 |

不配置视觉模型时，图片模块只出规则分，语义评审标记为 `skipped`，不影响其他模块。

## 使用

1. 打开 `http://127.0.0.1:8010`，选择「按 ASIN」或「按关键词」。
2. 采集完成后下载压缩包，或点「开始评分」进入 `/analysis?collection=<id>`。
3. 评分页自动运行三个模块；点任意一行展开子项、问题建议与事实卡。
4. 标题 / 五点卡片底部可打开中英对照弹窗；图片卡片可查看全部主图与 A+ 图并放大。
5. 历史批次可通过 `/?collection=<id>` 或 `/analysis?collection=<id>` 直接打开。

## 评分说明

| 模块 | 内容 |
| --- | --- |
| 标题 | 长度、结构完整性、亮点互补、关键词覆盖、堆砌风险、可读性 |
| 五点 | 完整性、首句吸引力、卖点与场景覆盖、格式、关键词、重复度 |
| 图片 | 主图数量、白底规范、长宽比、主体占比、A+ 数量、变体覆盖 |
| 语义评审（可选） | 场景表达、细节覆盖、A+ 信息设计、主图纯净度，各 25 分 |

- 三个模块满分均为 100，**不合成链接总分**，由使用者自行判断。
- 缺失字段只记录在 `data_gaps`，不转成扣分。
- 视觉评审默认只送 3 张主图 + 2 张 A+ 图，控制成本与延迟。

## 输出结构

```
output/<collection_id>/
├─ data.json                # 结构化资料，供评分与接口读取
├─ manifest.json            # 批次元信息，最后写入
├─ images/<ASIN>/
│   ├─ main_*.jpg
│   ├─ aplus_*.jpg
│   └─ variation.png
├─ 竞品LISTING分析_YYYYMMDD_HHMM.xlsx
├─ translations.json        # 中译缓存（按需生成）
└─ 打包下载.zip              # 对外只含 Excel 与 images
```

`output/` 为运行时目录，已在 `.gitignore` 中忽略。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/jobs` | 创建采集任务 |
| GET | `/api/jobs/{job_id}` | 查询任务进度 |
| POST | `/api/discover` | 关键词候选搜索 |
| GET | `/api/collections/{cid}` | 读取结构化采集结果 |
| GET | `/api/collections/{cid}/package` | 下载 ZIP |
| GET | `/api/collections/{cid}/files/{path}` | 读取图片等资料 |
| POST | `/api/collections/{cid}/analyses` | 运行评分 |
| GET | `/api/collections/{cid}/analyses` | 历史评分列表 |
| GET | `/api/collections/{cid}/analyses/{aid}` | 单次评分详情 |
| POST | `/api/collections/{cid}/translate` | 标题 / 五点中译（带缓存） |

## 部署

任务队列与进度保存在进程内存，**只支持单进程运行**，不要开多 worker：

```bash
python web/app.py
```

Linux 服务器首次部署需要安装浏览器依赖：

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env    # 填写密钥
```

生产环境建议用 systemd 守护，前置 Nginx 反向代理到 `127.0.0.1:8010`。`output/` 会持续增长，记得定期清理或做磁盘监控。

## 免责声明

本项目仅用于对亚马逊公开商品信息的竞品研究与学习。请遵守目标站点的服务条款与访问频率限制，控制并发与请求间隔；因使用不当导致的账号或网络风险由使用者自行承担。
