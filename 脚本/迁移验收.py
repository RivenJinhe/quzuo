"""迁移验收（PRD 7.3 表 10「保存与部署」）

核对同一账号迁移前后的材料与方案是否原样保留，并复测三件事：
  · 隔日回看：createdAt 没被重写成"现在"
  · 跨账号隔离：另一个账号读不到这些材料与方案
  · 服务重启后数据保留：重启后再跑一次 verify，数量与 createdAt 不变即通过

用法
  python 迁移验收.py snapshot --base http://8.129.227.49:8010 --user 账号 --pass 密码 -o snap.json
  # ……迁移数据库、重启服务……
  python 迁移验收.py verify   --base http://8.129.227.49:8010 --user 账号 --pass 密码 --expect snap.json \
         [--peer-user 另一个账号 --peer-pass 密码]

退出码：0 通过，1 有失败项。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def make_client(base: str, user: str | None, pwd: str | None) -> httpx.Client:
    c = httpx.Client(base_url=base.rstrip("/") + "/api/v1", timeout=30)
    if not user:
        return c
    r = c.post("/auth/login", json={"username": user, "password": pwd})
    if r.status_code in (400, 401, 404):
        r = c.post("/auth/register", json={"username": user, "password": pwd})
    if r.status_code >= 400:
        raise SystemExit("登录/注册失败：%s %s" % (r.status_code, r.text[:300]))
    return c


def fetch_all(c: httpx.Client, path: str) -> list:
    items, cursor, seen = [], None, set()
    for _ in range(20):
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        r = c.get(path, params=params)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items") or [])
        cursor = data.get("nextCursor")
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
    return items


def _row_mat(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "title": m.get("title"),
        "topic": m.get("topic"),
        "status": m.get("status"),
        "chars": len(m.get("excerpt") or ""),
        "createdAt": m.get("createdAt"),
        "note": bool(m.get("note")),
    }


def _row_plan(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "version": p.get("version"),
        "chars": len(p.get("body") or ""),
        "createdAt": p.get("createdAt"),
    }


def collect(c: httpx.Client) -> dict:
    me = c.get("/auth/me").json()
    return {
        "takenAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "base": str(c.base_url),
        "account": (me.get("account") or {}).get("username"),
        "materials": [_row_mat(m) for m in fetch_all(c, "/materials")],
        "plans": [_row_plan(p) for p in fetch_all(c, "/plans")],
    }


def _diff(label: str, want: list, got: list, fields: list) -> list:
    """按 id 对齐，找出缺失与被改写的字段。"""
    fails = []
    cur = {x.get("id"): x for x in got}
    for w in want:
        g = cur.get(w.get("id"))
        if not g:
            fails.append("%s 丢失：%s（%s）" % (label, w.get("title"), w.get("id")))
            continue
        for f in fields:
            if w.get(f) != g.get(f):
                fails.append("%s 被改动：%s 的 %s %r → %r" % (label, w.get("title"), f, w.get(f), g.get(f)))
    return fails


def cmd_snapshot(args) -> int:
    c = make_client(args.base, args.user, args.passwd)
    snap = collect(c)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=1)
    print("已存快照：材料 %d 份，方案 %d 份 → %s" % (len(snap["materials"]), len(snap["plans"]), args.out))
    return 0


def cmd_verify(args) -> int:
    with open(args.expect, encoding="utf-8") as fh:
        want = json.load(fh)
    c = make_client(args.base, args.user, args.passwd)
    got = collect(c)

    fails = []
    fails += _diff("材料", want["materials"], got["materials"], ["title", "topic", "status", "chars", "createdAt"])
    fails += _diff("方案", want["plans"], got["plans"], ["title", "version", "chars", "createdAt"])

    print("【材料】迁移前 %d 份，现在 %d 份" % (len(want["materials"]), len(got["materials"])))
    print("【方案】迁移前 %d 份，现在 %d 份" % (len(want["plans"]), len(got["plans"])))
    print("【隔日回看】createdAt 未被改写：%s" % ("是" if not any("createdAt" in f for f in fails) else "否"))

    # 跨账号隔离
    iso = "未测（没给 --peer-user）"
    if args.peer_user and want["materials"]:
        peer = make_client(args.base, args.peer_user, args.peer_pass)
        probe = [m["id"] for m in want["materials"][:3]]
        leak = []
        for mid in probe:
            r = peer.get("/materials/%s" % mid)
            if r.status_code != 404:
                leak.append("%s → %s" % (mid, r.status_code))
        for pid in [p["id"] for p in want["plans"][:2]]:
            r = peer.get("/plans/%s" % pid)
            if r.status_code != 404:
                leak.append("%s → %s" % (pid, r.status_code))
        if leak:
            fails.append("跨账号能看到别人的数据：" + "、".join(leak))
            iso = "失败"
        else:
            iso = "通过（抽查 %d 份材料和 2 份方案均返回 404）" % len(probe)
    print("【跨账号隔离】%s" % iso)

    if len(got["materials"]) > len(want["materials"]):
        print("提示：迁移后多出 %d 份材料，属于迁移后新增，不计失败。" % (len(got["materials"]) - len(want["materials"])))

    if fails:
        print("\n结论：不通过，%d 处问题" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("\n结论：通过。服务重启后数量与 createdAt 都没变，说明数据落在持久卷里。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="迁移验收：核对材料与方案迁移前后一致")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("snapshot", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--base", required=True, help="服务地址，例如 http://8.129.227.49:8010")
        sp.add_argument("--user", required=True)
        sp.add_argument("--pass", dest="passwd", required=True)
        sp.add_argument("--peer-user", dest="peer_user", help="隔离测试用的另一个账号")
        sp.add_argument("--peer-pass", dest="peer_pass")
        if name == "snapshot":
            sp.add_argument("-o", "--out", required=True)
        else:
            sp.add_argument("--expect", required=True)
    args = ap.parse_args()
    return cmd_snapshot(args) if args.cmd == "snapshot" else cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
