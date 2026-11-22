#!/usr/bin/env bash
# 起数据库（前台常驻；Ctrl+C 停）
set -e
cd "$(dirname "$0")/.."
PGDATA_DIR="${QU_PGDATA:-F:/qu_pg/data}"
PORT="${QU_PGPORT:-55432}"
echo "postgres data=$PGDATA_DIR port=$PORT"
exec ./pgsql17_root/pgsql/bin/postgres.exe -D "$PGDATA_DIR" -p "$PORT"
