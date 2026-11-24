#!/usr/bin/env bash
# 打线上部署包：解包后目录是扁平的（Dockerfile / app / web / requirements.txt 同级）。
# 用法：bash 去做/脚本/打包部署包.sh
set -euo pipefail
ROOT="F:/DSH工作区"
PROJ="$ROOT/去做"
STAGE="$ROOT/_tmp/qudeploy"
NAME="quzuo-deploy-$(date +%Y-%m-%d-%H%M).tar.gz"
OUT="$PROJ/交付物/$NAME"

rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -r "$PROJ/后端/app" "$STAGE/app"
cp -r "$PROJ/后端/web" "$STAGE/web"
cp "$PROJ/后端/requirements.txt" "$STAGE/"
cp "$PROJ/Dockerfile.deploy" "$STAGE/Dockerfile"
cp "$PROJ/docker-compose.yml" "$STAGE/"
cp "$PROJ/.env.prod" "$STAGE/"
cp "$PROJ/.env" "$STAGE/"
find "$STAGE" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

tar czf "$OUT" -C "$ROOT/_tmp" qudeploy
echo "打包完成: $OUT"
echo "条目数: $(tar -tzf "$OUT" | tr -d '\r' | wc -l)"
echo "非 ASCII 条目: $(tar -tzf "$OUT" | tr -d '\r' | LC_ALL=C grep -c '[^ -~]' || true)"
tar -xzOf "$OUT" qudeploy/Dockerfile | grep -E '^COPY|^CMD'