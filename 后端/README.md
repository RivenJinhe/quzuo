# 去做 · 后端（半验收版）

FastAPI + PostgreSQL，覆盖 PRD v0.2.4 首版 PO 的六步流程：存材料 → 检索确认 → 生成方案 → 同对话改条件 → 保存 → 回看/复制/看出处。
需求条目见 `../交付物/去做_后端半验收需求说明_v0.1.md`（BE-xxx 编号）。

## 1. 目录

```
app/
  main.py                入口：CORS、错误体、/health、/api/v1/me、dev 端点、静态前端挂载
  config.py              QU_ 前缀配置（pydantic-settings）
  db.py                  engine / SessionLocal / get_db
  models.py              7 张表：trial_user / account / auth_token / material / conversation / generation / citation / plan / generation_log
  errors.py              ApiError + 统一错误体 {error:{code,message,field,details}}
  security.py            试用 token、PBKDF2 口令哈希
  deps.py                get_trial / require_writable / require_readable / get_account
  schemas.py             请求响应模型
  routers/               materials / conversations / plans / citations / models / auth
  services/
    extract.py           文件转 Markdown（pdf/docx/pptx/txt）
    retrieval.py         材料检索打分
    generate.py          入参白名单、提示词、模型调用、出处快照、异步生成
    providers.py         模型目录（DeepSeek/Kimi/Qwen/MiniMax + 内部 stub）
    turns.py             自然语言追问解析（一句话 → 条件增量）
    validation.py        程序侧八项复核
web/                     前端测试台（纯静态，无构建）
scripts/                 start_db.sh / start_api.sh / ac_acceptance.py
tests/                   pytest 单测（validation 9 例 + turns 12 例）
```

## 2. 起服务

```bash
# 1) 数据库（PG17 免安装 zip，本机 55432 端口）
cd F:/DSH工作区/去做/后端
pgsql17_root/pgsql/bin/postgres.exe -D F:/qu_pg/data -p 55432

# 2) API（0.0.0.0:8010）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010

# 3) 打开
#    本机 http://127.0.0.1:8010/app/
#    同内网 http://<本机IP>:8010/app/
#    接口文档 http://127.0.0.1:8010/docs
```

首次建库（免安装 zip 在中文 Windows 上的 workaround）：

```bash
pgsql17_root/pgsql/bin/initdb.exe -D F:/qu_pg/data -U qu -E SQL_ASCII --locale=C --auth=trust
pgsql17_root/pgsql/bin/createdb.exe -h 127.0.0.1 -p 55432 -U qu -E UTF8 -T template0 qu_dev
```

> 中文路径下 `initdb -E UTF8` 会报 `invalid byte sequence for encoding "UTF8": 0xb9`（post-bootstrap 导入本地化 collation 名）。
> 集群用 SQL_ASCII 建、应用库用 UTF8 建，业务读写中文正常。
> 连接串：`postgresql+psycopg://qu:qu@127.0.0.1:55432/qu_dev`。

## 3. 配置（`.env`，前缀 QU_）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| QU_DATABASE_URL | 本机 qu_dev | 数据库连接串 |
| QU_COOKIE_SECURE | false | 线上 HTTPS 时置 true |
| QU_TRIAL_TTL_DAYS | 30 | 匿名试用有效期（天） |
| QU_TRIAL_READONLY_DAYS | 30 | 到期后只读天数 |
| QU_TRIAL_PURGE_DAYS | 37 | 彻底过期/清理（天） |
| QU_LLM_ALLOW_REAL | false | 只有 true 才允许调真实模型 |
| QU_LLM_DEFAULT_MODEL | deepseek/deepseek-chat | 默认模型 |
| QU_LLM_MAX_COMPARE | 4 | 一次最多对比几个模型 |
| QU_LLM_TIMEOUT_SECONDS | 180 | 单次模型调用超时 |
| QU_PROVIDER_DEEPSEEK_KEY / _KIMI_ / _QWEN_ / _MINIMAX_ | 空 | 四家密钥，仅服务端持有 |
| QU_GEN_RATE_PER_MINUTE | 6 | 生成限流 |
| QU_MAX_EXCERPT_CODEPOINTS | 12000 | 一次送模型的摘录合计上限 |

## 4. 接口一览（前缀 /api/v1）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /materials | 新增材料（201） |
| GET | /materials | 列表（topic / q / 游标分页） |
| GET | /materials/topics | 主题聚合 |
| POST | /materials/batch | 批量取 |
| POST | /materials/extract | 上传文件转 Markdown（不落库） |
| GET/PATCH/DELETE | /materials/{id} | 详情 / 改（version 乐观锁）/ 软删 |
| POST | /conversations | 建对话并返回检索候选 |
| GET | /conversations/{id} | 对话详情 |
| GET | /conversations/{id}/retrieve | 对话内重新检索 |
| POST | /conversations/{id}/materials/confirm | 确认采用材料 |
| PATCH | /conversations/{id}/conditions | 改目标/条件/保留项（version 锁，作废旧生成） |
| POST | /conversations/{id}/turns | 自然语言追问：一句话改条件并当场重排（202） |
| POST | /conversations/{id}/generations | 发起生成（202，支持 model_ids 多模型对比） |
| GET | /generations/{gid} | 轮询生成状态 |
| POST | /generations/{gid}/cancel | 取消 |
| GET | /conversations/{id}/generations | 该对话的生成列表 |
| POST | /plans | 保存方案（同对话重复保存=更新原记录） |
| GET | /plans | 我的方案列表 |
| GET/PATCH/DELETE | /plans/{id} | 打开 / 改标题（version）/ 删除 |
| POST | /plans/{id}/copy | 复制公开版（剥私人备注与选择理由） |
| GET | /citations/{cid} | 出处展开（摘录快照 + 文件/页码 + 可跳转 URL） |
| GET | /generations/{gid}/citations | 某次生成的全部出处 |
| GET | /plans/{pid}/citations | 某份方案的出处 |
| GET | /models | 可选模型目录（不含密钥） |
| POST | /auth/register /login /logout | 账号（游客数据直接绑定） |
| GET | /auth/me | 当前身份 |

错误体统一为 `{"error":{"code":"...","message":"..."}}`；常见码：VALIDATION_ERROR / NOT_FOUND / VERSION_CONFLICT / CONFLICT_REQUIREMENTS / SOURCE_INSUFFICIENT / STALE_GENERATION / RATE_LIMITED / TRIAL_EXPIRED(_READONLY)。

## 5. 上传文件：先转 Markdown 再给模型

- `POST /materials/extract`（multipart `file`，可选 `page_start`/`page_end`）把文件转成 Markdown 文本，**不落库**，前端拿回来填进材料表单的「摘录原文」。
- 支持 `.pdf`（pymupdf4llm，识别标题与表格）、`.docx`（标题层级/列表/表格）、`.pptx`（按页）、`.txt/.md/.csv`；≤20MB，≤300 页，正文截断到 20000 字。
- 多页时每页前置 `<!-- page N -->` 标记，保证生成方案时出处页码可回溯。
- 材料 `excerpt` 上限 20000 字；一次送模型的摘录合计上限 12000 字（超了要求用户减少材料）。
- 不做 OCR、不还原版式、不解析整份 PPT/PDF 的版面结构；图片型文件会返回「没有可提取的文字」。
- 前端材料表单上方可选文件，选中后自动填标题/文件名/页码/摘录，并出现「下载 .md」按钮。

## 6. 生成链路把关

送模型的只有：目标、任务类型、本次选定材料的摘录与来源、用户条件、保留项（已确认重点/安排）、待确认事实；关闭联网搜索；密钥只在服务端。

生成后用 `app/services/validation.py` 做八项程序侧复核（模型返回的 JSON 只是候选，程序说了算）：

| 校验 | 做什么 | 失败怎么处置 |
| --- | --- | --- |
| STRUCTURE | 必要字段齐全 | block（不返回正文） |
| CITATION_GATE | 引用必须落在材料摘录内、页码在范围内 | 个别剔除→notice；全被剔除→block SOURCE_INSUFFICIENT |
| NUMBER_WHITELIST | detail 里的数字要能在材料/目标/条件里找到 | notice + 记 pending_facts |
| DURATION_TOTAL | 时长加总 vs 可用时长（review 取 duration_minutes，activity 取 total_minutes，travel 取 available_minutes） | 必选项就超→block CONFLICT_REQUIREMENTS 并给放宽选项；只是总量超→notice |
| MATERIAL_DERIVE | 物料随环节派生（题卡与「猜词」绑定且数量=人数、名牌=人数、桌椅固定不可变） | 自动改正并记 corrections |
| BUDGET | 费用加总、人均 vs 预算 | notice + 差额 + 放宽选项；缺单价记 pending_facts |
| TIMELINE | 时间线到离时刻推算（起点+去程=到店，+时长=离开，+返程=回到） | 与声明不一致则重算覆盖并记 correction |
| RETAINED_COVERED | 用户说「一定要讲/务必保留」的项要在结果里出现 | 缺了→notice + 建议补一条（不替换模型输出） |

引用匹配做了 Markdown 归一化（剥 `# | ** <br> <!-- page N -->` 等标记），所以材料是 Markdown 也不会误杀。

## 6.5 自然语言追问（PRD 5.2「可以追问」）

`POST /conversations/{id}/turns` body `{text, version?, regenerate?, model_ids?}`，一次调用完成：记一条追问 → 把一句话解析成条件增量 → version+1 → 作废该对话进行中的生成 → 当场按新条件重排（202）。

- 能算成数字的必须落进结构化条件，才能继续走程序复核：`时间只剩 5 分钟了` → `duration_minutes=5`；`人均预算 60` → `budget_per_person=60`；`总共 300 块，20 人` → `budget_per_person=15`；`删掉猜词` → 从 `items` 里移除（连带物料由 MATERIAL_DERIVE 处理）；`15:15 出发` → `start_time`。
- 算不出数字的原句进 `conditions.user_notes`，随条件一起交给模型（最多 10 条），不影响复核数字。
- `例题一定要讲` 这类保留要求写进 `retained.must_keep_texts`，第 8 项复核 `RETAINED_COVERED` 检查结果里是否真的出现。
- 解析结果回给前端 `{applied:[{field,from,to,label}], unresolved:[...], summary}`，前端显示「已应用/未识别」。
- 解析器是确定性的（`app/services/turns.py`），不调模型，单测 12 例覆盖。

## 7. 模型目录

`GET /models` 返回：deepseek/deepseek-chat、kimi/kimi-k2.6、kimi/kimi-k3、qwen/qwen-plus、qwen/qwen4-max、minimax/MiniMax-M1。
前端可多选对比；`POST /conversations/{id}/generations` 带 `model_ids`，每个模型一条生成记录，各跑一次完整复核。
内部 `stub/local` 不对外暴露，仅验收脚本 `scripts/ac_acceptance.py` 用（`setdefault("model_ids", ["stub/local"])`），保证 63 项断言不依赖外部模型。

## 8. 身份与试用期

- 游客：首次请求下发 `qu_trial` cookie（HttpOnly/SameSite=Lax，库内只存 sha256），30 天有效，之后 7 天只读，37 天后 403 TRIAL_EXPIRED。
- 注册/登录：当前游客身份的数据直接绑定账号，多设备登录同一账号可共享数据；登录后有效期 100 年。
- 所有资源带 `owner_id`，跨身份读写一律 404（不泄露存在性）。

## 9. 测试与验收

```bash
cd F:/DSH工作区/去做/后端
python -m pytest -q          # 单测（21 项，只看 tests/，见 pytest.ini）
python scripts/ac_acceptance.py   # 半验收 63 项，需 API 在 8010 跑着
```

- AC-001 复习 17 项、AC-002 出行 7 项、AC-003 活动 8 项、AC-004 隔离与过期 8 项、AC-005 异常恢复 5 项、AC-006 并发与时序 6 项、AC-007 自然语言追问 12 项，共 63 项，当前全过。
- 验收脚本用 stub 模型跑数据链路；真实模型另有手工验证（四家都跑通 15 分钟复习用例）。

## 10. 已知限制

- `/internal/dev/*` 三个调试端点无鉴权（造 pending 生成、注入 result、改试用期），上线前必须摘掉。
- 限流是单进程内存计数，多进程/多实例下不准。
- 生成是进程内 asyncio 后台任务，API 进程重启会丢失进行中的生成（状态停在 running）。
- 不做整份 PPT/PDF 版面解析、OCR、语音、视频、跨平台同步、实时地图订位、多人协作、并排版本对比。
- 前端是测试台级别，不是产品 UI。

## 内网穿透（临时演示用）

本机起 cloudflared 快速隧道，公网 https 地址随进程存活，关掉即失效：

```bash
./tools/cloudflared.exe tunnel --url http://127.0.0.1:8010 \
  --protocol http2 --edge-ip-version 4 --no-autoupdate \
  --logfile ../过程文件/cf2.log --loglevel info
grep -o -E 'https://[a-z0-9-]+\.trycloudflare\.com' ../过程文件/cf2.log | head -1
```

必须带 `--protocol http2`：默认走 QUIC，在国内网络下到 Cloudflare 边缘经常握手超时
（日志 `failed to dial to edge with quic: timeout: no recent network activity`），
进程还在、地址也在，但所有请求返回 530 / Error 1033。切 http2 后稳定。

拿到 `https://xxx.trycloudflare.com` 后，`/app/` 与 `/docs` 都能从任意网络访问（手机 4G 也可以）。
注意两点：

- 隧道的域名等于一个新的 cookie 域，所以它是**一份新的游客身份**，看不到本机 127.0.0.1 下的数据；要跨设备看同一份数据，用「登录/注册」账号登录。
- `/internal/dev/*` 已经被 `_dev_local_only()` 保护：请求带 `CF-Connecting-IP` / `X-Forwarded-For` / `CF-Ray` 一律 403，所以经隧道访问不到「改试用期」「注入生成结果」。本机直连仍然可用，验收脚本不受影响。
