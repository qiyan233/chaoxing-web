FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml uv.lock uv.toml /app/

RUN pip install --no-cache-dir uv -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --no-dev \
    && rm -rf /tmp/uv-cache

COPY . /app

RUN mkdir -p /app/data

VOLUME /app/data

EXPOSE 3000

ENTRYPOINT ["/app/.venv/bin/python", "start.py", "--host", "0.0.0.0", "--port", "3000"]
