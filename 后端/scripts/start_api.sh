#!/usr/bin/env bash
# 起接口服务
set -e
cd "$(dirname "$0")/.."
exec python -m uvicorn app.main:app --host 127.0.0.1 --port "${QU_PORT:-8010}"
