# 云端部署用镜像。
# 基于 Playwright 官方镜像：已内置 Chromium 和全部 Linux 系统库，无需再装 apt 依赖。
# 版本必须与项目安装的 playwright Python 包一致，否则会因浏览器版本不匹配而重新下载。
ARG PW_VERSION=1.65.2
FROM mcr.microsoft.com/playwright/python:v${PW_VERSION}-noble

ARG PW_VERSION=1.65.2
# 国内构建加速：pip 与 playwright 浏览器下载源，可在 docker compose build 时覆盖
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST} \
    PORT=8010

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt -i ${PIP_INDEX_URL} \
    && pip install --no-cache-dir "playwright==${PW_VERSION}" -i ${PIP_INDEX_URL}

# 版本一致时此处直接跳过；仅在不匹配时补下载
RUN python -m playwright install chromium

COPY . .

RUN mkdir -p /app/output

EXPOSE 8010

# 注意：web/app.py 内 uvicorn 监听固定为 127.0.0.1，容器内必须用 CLI 覆盖为 0.0.0.0，
# 否则端口映射后外部仍无法访问。--app-dir 保证模块导入路径正确。
# 本项目任务队列在进程内存中，只能单进程，不要加 --workers。
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8010", "--app-dir", "/app", "--log-level", "info"]
