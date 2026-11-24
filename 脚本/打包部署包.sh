#!/usr/bin/env bash
# 构建双入口前端，生成可直接用 Docker Compose 启动的部署包。
# 用法：从仓库根目录运行 bash 脚本/打包部署包.sh
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
pnpm --dir "$project/前端" install --frozen-lockfile
pnpm --dir "$project/前端" build

temp_dir=$(mktemp -d)
trap 'rm -rf -- "$temp_dir"' EXIT
stage="$temp_dir/qudeploy"
mkdir -p "$stage/web"
cp -R "$project/后端/app" "$stage/app"
cp -R "$project/后端/web/dist" "$stage/web/dist"
cp "$project/后端/requirements.txt" "$project/docker-compose.yml" "$project/.env.prod.example" "$stage/"
cp "$project/Dockerfile.deploy" "$stage/Dockerfile"
for config in .env.prod .env; do
  if [[ -f "$project/$config" ]]; then cp "$project/$config" "$stage/"; fi
done

out="$project/交付物/quzuo-deploy-$(date +%Y-%m-%d-%H%M%S).tar.gz"
tar --exclude='__pycache__' --exclude='*.pyc' -czf "$out" -C "$temp_dir" qudeploy
echo "部署包已写入 $out"
