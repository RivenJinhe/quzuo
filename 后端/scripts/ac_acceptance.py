"""AC-001 ~ AC-006 验收脚本（打真实服务，默认 http://127.0.0.1:8010）。

前置：数据库已起、uvicorn 已起。
用法：python scripts/ac_acceptance.py
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.environ.get("QU_BASE", "http://127.0.0.1:8010")
PASS = 0
FAIL = 0


def check(code, title, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    line = ("[PASS] " if ok else "[FAIL] ") + code + " " + title
    if not ok:
        line += "   << " + str(detail)[:400]
    print(line)


def client():
    return httpx.Client(base_url=BASE, timeout=60)


def make_material(c, **kw):
    r = c.post("/api/v1/materials", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


def make_conv(c, goal, task_type, material_ids=None, conditions=None):
    r = c.post("/api/v1/conversations", json={
        "goal": goal, "task_type": task_type,
        "material_ids": material_ids or [], "conditions": conditions or {},
    })
    assert r.status_code == 201, r.text
    return r.json()


def patch_conditions(c, cid, cond, retained=None):
    cur = c.get("/api/v1/conversations/%s" % cid).json()
    body = {"version": cur["version"], "conditions": cond}
    if retained is not None:
        body["retained"] = retained
    r = c.patch("/api/v1/conversations/%s/conditions" % cid, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def generate(c, cid, body=None):
    payload = dict(body or {})
    payload.setdefault("model_ids", ["stub/local"])
    r = c.post("/api/v1/conversations/%s/generations" % cid, json=payload)
    assert r.status_code == 202, r.text
    return r.json()


def wait_gen(c, gid, timeout=60):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = c.get("/api/v1/generations/%s" % gid).json()
        if last.get("status") not in ("pending", "running"):
            return last
        time.sleep(0.2)
    return last


def save_plan(c, cid, gid, title="我的方案", body="正文内容", private="私人备注", rationale="选择理由"):
    return c.post("/api/v1/plans", json={
        "conversation_id": cid, "generation_id": gid, "title": title,
        "body": body, "body_private_note": private, "rationale": rationale,
        "confirmed_conditions": {"note": "确认过"},
    })


def inject(c, cid, result):
    r = c.post("/internal/dev/generations/inject", json={"conversation_id": cid, "result": result})
    assert r.status_code == 200, r.text
    return r.json()


def total_minutes(result):
    return sum(int(i.get("minutes") or 0) for i in (result.get("items") or []))


def check_by(validation, code):
    for x in validation.get("checks") or []:
        if x["code"] == code:
            return x
    return {}


def notice_by(validation, code):
    for x in validation.get("notices") or []:
        if x["code"] == code:
            return x
    return {}


# ---------------- AC-001 复习：15 分钟压到 5 分钟 ----------------
def ac001():
    c = client()
    m = make_material(
        c, title="高数第 8-12 页", topic="高数",
        excerpt="第 8 页：例题讲解，先讲例题再总结。第 12 页：课后习题 1-3。",
        source_kind="courseware", source_file="高数课件.pdf",
        source_page_start=8, source_page_end=12,
    )
    check("AC-001-01", "材料保存成功（可引用）", m["status"] == "citable", m)

    d = make_conv(c, "复习第 8 到 12 页", "review", [m["id"]])
    cid = d["conversation"]["id"]
    check("AC-001-02", "创建对话即返回拟采用材料", len(d["candidates"]) >= 1, d["candidates"])

    r = c.post("/api/v1/conversations/%s/materials/confirm" % cid,
               json={"materials": [{"id": m["id"], "use": True, "reason": "就是这页"}]})
    check("AC-001-03", "确认材料后状态 confirmed",
          r.status_code == 200 and r.json()["status"] == "confirmed", r.text[:200])

    conv = patch_conditions(c, cid, {"available_minutes": 15})
    check("AC-001-04", "条件写入 15 分钟且 version 递增",
          conv["conditions"]["available_minutes"] == 15 and conv["version"] >= 3, conv)

    g1 = generate(c, cid)
    d1 = wait_gen(c, g1["id"])
    check("AC-001-05", "第一次生成成功", d1["status"] == "succeeded", d1)
    check("AC-001-06", "结果为 15 分钟", total_minutes(d1["result"]) == 15, total_minutes(d1["result"]))
    p1 = save_plan(c, cid, d1["id"], title="复习计划 v1", body="15 分钟版")
    check("AC-001-07", "保存方案成功", p1.status_code == 201, p1.text[:300])
    plan = p1.json()

    patch_conditions(c, cid, {"available_minutes": 5})
    g2 = generate(c, cid)
    d2 = wait_gen(c, g2["id"])
    check("AC-001-08", "改条件后重新生成成功", d2["status"] == "succeeded", d2)
    check("AC-001-09", "15 分钟压到 5 分钟", total_minutes(d2["result"]) == 5, total_minutes(d2["result"]))

    stale = save_plan(c, cid, d1["id"], title="旧版", body="旧正文")
    check("AC-001-10", "旧结果保存被拒（409 STALE_GENERATION）",
          stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_GENERATION", stale.text[:300])

    p2 = save_plan(c, cid, d2["id"], title="复习计划 v2", body="5 分钟版")
    check("AC-001-11", "新结果更新原记录而非新建",
          p2.status_code == 200 and p2.json()["id"] == plan["id"], p2.text[:300])
    check("AC-001-12", "原记录 version 递增",
          p2.json()["version"] == plan["version"] + 1, p2.json()["version"])

    lst = c.get("/api/v1/plans").json()
    check("AC-001-13", "我的方案列表只有 1 条", len(lst["items"]) == 1, lst)

    got = c.get("/api/v1/plans/%s" % plan["id"])
    check("AC-001-14", "回看方案正文为最新版",
          got.status_code == 200 and got.json()["body"] == "5 分钟版", got.text[:200])

    cp = c.post("/api/v1/plans/%s/copy" % plan["id"])
    text = cp.json()["text"]
    check("AC-001-15", "复制公开版去掉私人备注与选择理由",
          cp.status_code == 200 and "私人备注" not in text and "选择理由" not in text, text[:200])
    check("AC-001-16", "复制公开版保留正文", "5 分钟版" in text, text[:200])

    cits = c.get("/api/v1/plans/%s/citations" % plan["id"]).json()["items"]
    check("AC-001-17", "出处可展开且带文件名页码",
          len(cits) >= 1 and cits[0]["source_file"] == "高数课件.pdf"
          and cits[0]["source_page"] == "8-12", cits)


# ---------------- AC-002 出行：咖啡 10 分钟、超预算 15 元 ----------------
def ac002():
    c = client()
    m = make_material(
        c, title="美术馆参观笔记", topic="周末",
        excerpt="闭馆 16:30，步行 15 分钟到咖啡馆，人均 95 元。",
        source_kind="web", source_url="https://example.com/art-museum",
    )
    d = make_conv(c, "15:15 离馆，15:55 返校，中间想喝咖啡", "travel", [m["id"]], {
        "start_time": "15:15", "legs": [15, 15], "available_minutes": 10,
        "people": 1, "budget_per_person": 80,
    })
    cid = d["conversation"]["id"]
    c.post("/api/v1/conversations/%s/materials/confirm" % cid,
           json={"materials": [{"id": m["id"], "use": True}]})

    result = {
        "task_type": "travel",
        "headline": "美术馆—咖啡馆—返校",
        "items": [{
            "title": "咖啡馆小坐", "minutes": 30,
            "detail": "喝杯咖啡，人均 95 元",
            "citations": [{"material_id": m["id"], "excerpt": "步行 15 分钟到咖啡馆"}],
        }],
        "materials_needed": [{"name": "咖啡馆人均消费", "qty": 1, "unit_cost": 95}],
    }
    out = inject(c, cid, result)
    v = out["validation"]
    check("AC-002-01", "超预算只提示不阻断",
          v["block"] is None and out["status"] == "succeeded", out)

    dur = notice_by(v, "DURATION_TOTAL")
    check("AC-002-02", "咖啡想 30 分钟、实际最多 10 分钟",
          dur and dur["detail"]["total"] == 30 and dur["detail"]["available"] == 10, dur)

    bud = notice_by(v, "BUDGET")
    check("AC-002-03", "人均 95、预算 80，超 15 元",
          bud and bud["detail"]["per_person"] == 95.0 and bud["detail"]["diff"] == 15.0, bud)

    tl = check_by(v, "TIMELINE")["detail"]
    check("AC-002-04", "15:15 出发 → 15:30 到店", tl.get("arrive") == "15:30", tl)
    check("AC-002-05", "店内最多 10 分钟后 15:40 离开", tl.get("leave") == "15:40", tl)
    check("AC-002-06", "15:55 返校", tl.get("end") == "15:55", tl)

    opts = (bud or {}).get("options") or []
    check("AC-002-07", "给出可选放宽项", any(o.get("action") == "raise_budget" for o in opts), opts)


# ---------------- AC-003 活动：25 分钟、题卡随环节变 ----------------
def activity_result(mid, items, cards=True):
    mats = [
        {"name": "桌椅", "qty": 20, "unit_cost": 0},
        {"name": "名牌", "qty": 20, "unit_cost": 0},
    ]
    if cards:
        mats.insert(1, {"name": "题卡", "qty": 20, "unit_cost": 0})
    return {
        "task_type": "activity",
        "headline": "20 人班级活动",
        "items": items,
        "materials_needed": mats,
    }


def _items_spec(mid):
    return [
        {"title": "开场", "minutes": 2, "detail": "点名，2 分钟",
         "citations": [{"material_id": mid, "excerpt": "开场 2 分钟"}]},
        {"title": "互相参观", "minutes": 12, "must_keep": True, "detail": "两组互访 12 分钟",
         "citations": [{"material_id": mid, "excerpt": "互相参观 12 分钟"}]},
        {"title": "猜词", "minutes": 8, "detail": "用题卡猜词 8 分钟",
         "citations": [{"material_id": mid, "excerpt": "猜词 8 分钟"}]},
        {"title": "合影", "minutes": 3, "detail": "集体照 3 分钟",
         "citations": [{"material_id": mid, "excerpt": "合影 3 分钟"}]},
    ]


def ac003():
    c = client()
    m = make_material(
        c, title="班级活动流程草稿", topic="班级",
        excerpt="开场 2 分钟；互相参观 12 分钟；猜词 8 分钟；合影 3 分钟。",
        source_kind="manual",
    )
    d = make_conv(c, "20 人班级活动，桌椅不能动", "activity", [m["id"]], {
        "people": 20, "available_minutes": 25, "fixed_furniture": {"桌椅": 20},
    })
    cid = d["conversation"]["id"]
    c.post("/api/v1/conversations/%s/materials/confirm" % cid,
           json={"materials": [{"id": m["id"], "use": True}]})

    out = inject(c, cid, activity_result(m["id"], _items_spec(m["id"])))
    v = out["validation"]
    check("AC-003-01", "2+12+8+3=25 分钟通过",
          v["passed"] and out["status"] == "succeeded", v.get("block"))
    names = {x["name"]: x["qty"] for x in out["result"]["materials_needed"]}
    check("AC-003-02", "题卡 20 张、名牌 20 个、桌椅 20 套",
          names.get("题卡") == 20 and names.get("名牌") == 20 and names.get("桌椅") == 20, names)

    patch_conditions(c, cid, {"available_minutes": 10})
    out2 = inject(c, cid, activity_result(m["id"], _items_spec(m["id"])))
    v2 = out2["validation"]
    b = v2.get("block") or {}
    check("AC-003-03", "总时长缩到 10 分钟被阻断",
          out2["status"] == "blocked" and b.get("code") == "CONFLICT_REQUIREMENTS", b)
    check("AC-003-04", "互访 12 分钟在 10 分钟下超出 2 分钟",
          b.get("detail", {}).get("required") == 12
          and b.get("detail", {}).get("available") == 10
          and b.get("detail", {}).get("diff") == 2, b.get("detail"))
    check("AC-003-05", "返回可选放宽项",
          any(o.get("action") == "extend_available" and o.get("extra_minutes") == 2
              for o in (b.get("options") or [])), b.get("options"))

    patch_conditions(c, cid, {"available_minutes": 25})
    no_guess = [i for i in _items_spec(m["id"]) if "猜词" not in i["title"]]
    out3 = inject(c, cid, activity_result(m["id"], no_guess, cards=True))
    names3 = {x["name"]: x["qty"] for x in out3["result"]["materials_needed"]}
    check("AC-003-06", "删掉猜词后总时长 17 分钟", total_minutes(out3["result"]) == 17, total_minutes(out3["result"]))
    check("AC-003-07", "题卡随猜词环节一起删掉", "题卡" not in names3, names3)
    check("AC-003-08", "删题卡写入 corrections",
          any(x.get("code") == "MATERIAL_REMOVED" for x in out3["validation"]["corrections"]),
          out3["validation"]["corrections"])


# ---------------- AC-004 隔离与过期 ----------------
def ac004():
    a = client()
    b = client()
    m = make_material(a, title="A 的材料", topic="私有", excerpt="只有 A 能看到的内容", source_kind="manual")
    d = make_conv(a, "A 的对话", "review", [m["id"]], {"available_minutes": 5})
    cid = d["conversation"]["id"]
    a.post("/api/v1/conversations/%s/materials/confirm" % cid,
           json={"materials": [{"id": m["id"], "use": True}]})
    g = generate(a, cid)
    gd = wait_gen(a, g["id"])
    p = save_plan(a, cid, gd["id"])
    pid = p.json()["id"]

    check("AC-004-01", "B 读不到 A 的材料", b.get("/api/v1/materials/%s" % m["id"]).status_code == 404)
    check("AC-004-02", "B 读不到 A 的方案", b.get("/api/v1/plans/%s" % pid).status_code == 404)
    check("AC-004-03", "B 的方案列表为空", len(b.get("/api/v1/plans").json()["items"]) == 0)
    check("AC-004-04", "B 的材料列表为空", len(b.get("/api/v1/materials").json()["items"]) == 0)
    check("AC-004-05", "A 自己看得到材料与方案",
          a.get("/api/v1/materials/%s" % m["id"]).status_code == 200
          and a.get("/api/v1/plans/%s" % pid).status_code == 200)

    me = a.get("/api/v1/me").json()
    a.post("/internal/dev/trials/%s/expire?mode=readonly" % me["trial_id"])
    r6 = a.get("/api/v1/plans/%s" % pid)
    check("AC-004-06", "只读期读得到旧数据", r6.status_code == 200, r6.text[:200])
    r = a.post("/api/v1/materials", json={"title": "x", "topic": "y", "excerpt": "z"})
    check("AC-004-07", "只读期不能再建材料（403 TRIAL_EXPIRED_READONLY）",
          r.status_code == 403 and r.json()["error"]["code"] == "TRIAL_EXPIRED_READONLY", r.text[:200])

    a.post("/internal/dev/trials/%s/expire?mode=expired" % me["trial_id"])
    r2 = a.get("/api/v1/plans/%s" % pid)
    check("AC-004-08", "彻底过期后数据不可访问（403 TRIAL_EXPIRED）",
          r2.status_code == 403 and r2.json()["error"]["code"] == "TRIAL_EXPIRED", r2.text[:200])


# ---------------- AC-005 异常：取消后保留、复制兜底 ----------------
def ac005():
    c = client()
    m = make_material(c, title="断网测试材料", topic="异常",
                      excerpt="这段材料在生成失败后仍要保留。", source_kind="manual")
    d = make_conv(c, "测试异常保留", "review", [m["id"]], {"available_minutes": 5})
    cid = d["conversation"]["id"]
    c.post("/api/v1/conversations/%s/materials/confirm" % cid,
           json={"materials": [{"id": m["id"], "use": True}]})

    g = generate(c, cid)
    c.post("/api/v1/generations/%s/cancel" % g["id"])
    conv = c.get("/api/v1/conversations/%s" % cid).json()
    check("AC-005-01", "取消生成后材料仍在",
          m["id"] in [str(x) for x in conv["material_ids"]], conv["material_ids"])
    check("AC-005-02", "取消生成后已填条件仍在",
          conv["conditions"].get("available_minutes") == 5, conv["conditions"])

    lst = c.get("/api/v1/materials", params={"q": "断网测试"}).json()
    check("AC-005-03", "断网恢复后可按标题找回材料", len(lst["items"]) == 1, lst)

    g2 = generate(c, cid)
    d2 = wait_gen(c, g2["id"])
    check("AC-005-04", "恢复后可直接重试生成", d2["status"] == "succeeded", d2.get("status"))
    p = save_plan(c, cid, d2["id"], title="兜底方案", body="正文", private="私", rationale="理")
    cp = c.post("/api/v1/plans/%s/copy" % p.json()["id"])
    check("AC-005-05", "复制接口始终返回正文（前端可手动选取）",
          cp.status_code == 200 and cp.json()["text"].strip() != "", cp.text[:200])


# ---------------- AC-006 并发与时序 ----------------
def ac006():
    c = client()
    m = make_material(c, title="并发材料", topic="并发", excerpt="用于验证最后一次输入为准。", source_kind="manual")
    d = make_conv(c, "并发测试", "review", [m["id"]], {"available_minutes": 10})
    cid = d["conversation"]["id"]
    c.post("/api/v1/conversations/%s/materials/confirm" % cid,
           json={"materials": [{"id": m["id"], "use": True}]})

    p = c.post("/internal/dev/generations/pending", json={"conversation_id": cid})
    check("AC-006-01", "可以造出一条 pending 生成", p.status_code == 200, p.text[:200])
    pending_id = p.json()["generation_id"]

    patch_conditions(c, cid, {"available_minutes": 5})
    st = c.get("/api/v1/generations/%s" % pending_id).json()["status"]
    check("AC-006-02", "改条件后旧生成被标记 superseded", st == "superseded", st)

    g = generate(c, cid)
    d1 = wait_gen(c, g["id"])
    check("AC-006-03", "新生成按最新条件 5 分钟", total_minutes(d1["result"]) == 5, total_minutes(d1["result"]))

    p1 = save_plan(c, cid, d1["id"], title="并发方案", body="v1")
    p2 = save_plan(c, cid, d1["id"], title="并发方案", body="v2")
    check("AC-006-04", "重复保存更新原记录",
          p1.status_code == 201 and p2.status_code == 200 and p1.json()["id"] == p2.json()["id"],
          p2.text[:200])
    check("AC-006-05", "重复保存后正文为最新",
          c.get("/api/v1/plans/%s" % p2.json()["id"]).json()["body"] == "v2")
    check("AC-006-06", "列表里仍然只有 1 条", len(c.get("/api/v1/plans").json()["items"]) == 1)


# ---------------- AC-007 自然语言追问 ----------------
def ac007():
    c = client()
    m = make_material(c, title="追问课件 第3章 极限", topic="追问",
                      excerpt="第3章 极限：若 lim f = A, lim g = B, 则 lim(f+g) = A+B。例：sin(x)/x 当 x 趋于 0 时极限为 1。",
                      source_kind="courseware", source_file="追问课件.pdf",
                      source_page_start=8, source_page_end=12)
    d = make_conv(c, "15 分钟复习极限，先讲例题", "review", [m["id"]], {"duration_minutes": 15})
    cid = d["conversation"]["id"]
    c.post("/api/v1/conversations/%s/materials/confirm" % cid,
           json={"materials": [{"id": m["id"], "use": True}]})

    g = generate(c, cid)
    d1 = wait_gen(c, g["id"])
    check("AC-007-01", "初始按 15 分钟排", total_minutes(d1["result"]) == 15, total_minutes(d1["result"]))

    r = c.post("/api/v1/conversations/%s/turns" % cid,
               json={"text": "时间只剩 5 分钟了，例题一定要讲", "model_ids": ["stub/local"]})
    b = r.json()
    check("AC-007-02", "追问返回 202 并当场重排",
          r.status_code == 202 and bool(b.get("generation")), r.text[:200])
    conv = b["conversation"]
    check("AC-007-03", "一句话改出 5 分钟条件", conv["conditions"].get("duration_minutes") == 5, conv["conditions"])
    check("AC-007-04", "保留项记下例题", "例题" in (conv["retained"].get("must_keep_texts") or []), conv["retained"])
    check("AC-007-05", "追问历史留痕", len(conv["turns"]) == 1, conv["turns"])

    d2 = wait_gen(c, b["generation"]["id"])
    check("AC-007-06", "重排结果按 5 分钟", total_minutes(d2["result"]) == 5, total_minutes(d2["result"]))
    codes = [x["code"] for x in (d2["validation"] or {}).get("checks") or []]
    check("AC-007-07", "第 8 项保留项复核存在", "RETAINED_COVERED" in codes, codes)
    check("AC-007-08", "新生成版本号前进", d1["conversation_version"] < d2["conversation_version"],
          (d1["conversation_version"], d2["conversation_version"]))

    p = save_plan(c, cid, d2["id"], title="追问方案", body="5 分钟版")
    old = c.post("/api/v1/plans", json={"conversation_id": cid, "generation_id": d1["id"],
                                        "title": "旧版", "body": "旧正文"})
    check("AC-007-09", "用旧生成保存被 409 拒绝",
          old.status_code == 409 and old.json()["error"]["code"] == "STALE_GENERATION", old.text[:200])
    check("AC-007-10", "已存方案没被旧结果覆盖",
          c.get("/api/v1/plans/%s" % p.json()["id"]).json()["body"] == "5 分钟版")

    b3 = c.post("/api/v1/conversations/%s/turns" % cid,
                json={"text": "还是 15 分钟吧", "model_ids": ["stub/local"]}).json()
    check("AC-007-11", "再说一句能改回 15 分钟",
          b3["conversation"]["conditions"].get("duration_minutes") == 15, b3["conversation"]["conditions"])
    back = c.get("/api/v1/conversations/%s" % cid).json()
    check("AC-007-12", "回看对话能看到全部追问", len(back["turns"]) == 2, len(back["turns"]))


def main():
    try:
        httpx.get(BASE + "/health", timeout=5).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print("服务没起来：%s（%s）" % (BASE, exc))
        sys.exit(2)
    for fn in (ac001, ac002, ac003, ac004, ac005, ac006, ac007):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(fn.__name__.upper() + "-ERR", "用例执行异常", False, repr(exc))
    print("")
    print("总计 %d 项，通过 %d 项，失败 %d 项" % (PASS + FAIL, PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
