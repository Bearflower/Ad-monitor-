FROM python:3.11-slim

WORKDIR /app

# 构建参数：pip 源（本地构建用清华源，CI 用默认）
ARG PIP_INDEX_URL=

# 只复制构建所需文件；运行时配置由 Compose 注入，不进入镜像层
COPY pyproject.toml VERSION ./
COPY src/ src/

# 安装依赖（非 editable 模式，避免 src/ 不存在问题）
RUN pip install --no-cache-dir ".[dev]" ${PIP_INDEX_URL:+--index-url $PIP_INDEX_URL}

# 数据目录（SQLite 持久化）
VOLUME /app/var

EXPOSE 8768

ENV ADWATCH_DATA_DIR=/app/var

CMD ["adwatch", "dashboard", "--host", "0.0.0.0", "--port", "8768", "--allow-remote"]
