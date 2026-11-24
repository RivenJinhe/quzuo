# 去做 · 后端容器镜像
# 供 Hugging Face Spaces (Docker SDK) 使用；本地 docker build 同样可用。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg[binary] 自带 libpq / libssl，无需 apt 安装系统依赖
COPY 后端/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 应用源码与静态前端（后端启动时把 ./web 挂到 /app 路径，所以两者要同级）
COPY 后端/app ./app
COPY 后端/web ./web

# Hugging Face Spaces 只放行 7860 端口
EXPOSE 7860

# 生成任务是「POST 202 + 轮询」，单请求不会挂太久，默认超时即可
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
