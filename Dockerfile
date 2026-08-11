FROM python:3.11-slim

WORKDIR /app

# 复制源码和配置文件
COPY pyproject.toml VERSION .env ./
COPY src/ src/

# 安装依赖（非 editable 模式，避免 src/ 不存在问题）
RUN pip install --no-cache-dir ".[dev]"

# 数据目录（SQLite 持久化）
VOLUME /app/var

EXPOSE 8768

ENV ADWATCH_DATA_DIR=/app/var

CMD ["adwatch", "dashboard", "--host", "0.0.0.0", "--port", "8768", "--allow-remote"]