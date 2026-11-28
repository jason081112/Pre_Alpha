# syntax=docker/dockerfile:1
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later

# ── 构建阶段：只装 Python 依赖，利用层缓存 ─────────────────────
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

# 国内机器可用 TUNA 加速：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /build
COPY vendor/OpenHer/requirements.txt ./requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install --index-url "${PIP_INDEX_URL}" -r requirements.txt

# ── 运行阶段 ──────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ALPHA_HOST=0.0.0.0 \
    ALPHA_PORT=8000 \
    ALPHA_DATA_DIR=/var/lib/prealpha

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . /app

# 引擎数据目录必须存在且可写（compose 会挂卷覆盖）
RUN mkdir -p /app/vendor/OpenHer/.data /var/lib/prealpha

EXPOSE 8000

# 进程活着就算健康；依赖问题看 /readyz
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["python", "-m", "shell.app"]
