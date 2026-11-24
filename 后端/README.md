# 去做后端

FastAPI 为[网页首版](../README.md)提供收藏、选材、生成、修改和保存接口，支持 SQLite 和 PostgreSQL。产品要求以 [PRD v0.2.5](../交付物/PRD_v0.2.5.md) 为准。

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
    validation.py        检查引用、时长、费用和场地条件
    plan_text.py         生成人可读的时间、费用、物料和公开正文
web/dist/                Vite 生成的产品页与开发测试台，挂载到 /app/
scripts/                 start_db.sh / start_api.sh / ac_acceptance.py
tests/                   纯逻辑单元测试
```

## 2. 起服务

前端源码位于[前端目录](../前端/)。要从 API 端口提供页面，先在仓库根目录运行 `pnpm --dir 前端 install --frozen-lockfile` 和 `pnpm --dir 前端 build`。开发时也可以通过 [Vite 开发服务器](../README.md#前端开发与构建)访问页面。

按[快速开始](../README.md#快速开始后端)安装依赖并将 `.env.example` 复制为 `.env`，然后在 `后端` 目录启动 API。

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

样例中的 `QU_DATABASE_URL=sqlite:///./qu_dev.sqlite3` 会在 `后端` 目录创建 SQLite 文件，应用启动时初始化表。页面地址为 `http://127.0.0.1:8010/app/`，接口文档为 `http://127.0.0.1:8010/docs`。如果要用同一内网的其他设备访问，将 `--host` 改为 `0.0.0.0`，用 `http://<本机IP>:8010/app/` 打开页面。

### 使用 PostgreSQL

启动 PostgreSQL 并创建 UTF-8 数据库，将 `.env` 中的 `QU_DATABASE_URL` 改为实际连接串，例如 `postgresql+psycopg://qu:qu@127.0.0.1:55432/qu_dev`，再启动 API。沿用 Windows 便携版 PostgreSQL 时，可用 `scripts/start_db.sh` 启动已初始化的实例，并通过 `QU_PGDATA` 和 `QU_PGPORT` 指定数据目录及端口。

如果 Windows 便携版 `initdb -E UTF8` 在导入本地化排序规则时遇到 `invalid byte sequence for encoding "UTF8"`，可以用 `SQL_ASCII` 初始化本机集群，再从 `template0` 创建 UTF-8 应用库。以下命令适用于原便携版目录，从 `后端` 运行。

```bash
pgsql17_root/pgsql/bin/initdb.exe -D F:/qu_pg/data -U qu -E SQL_ASCII --locale=C --auth=trust
bash scripts/start_db.sh
# 保持数据库运行，在另一个终端创建应用库
pgsql17_root/pgsql/bin/createdb.exe -h 127.0.0.1 -p 55432 -U qu -E UTF8 -T template0 qu_dev
```

部署时可通过环境变量提供 `QU_DATABASE_URL`。[Docker Compose 配置](../docker-compose.yml)使用 PostgreSQL。

## 3. 配置（`.env`，前缀 QU_）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| QU_DATABASE_URL | 本机 PostgreSQL `qu_dev` | SQLite 或 PostgreSQL 连接串，`.env.example` 预填 SQLite |
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
| QU_MAX_EXCERPT_CODEPOINTS | 40000 | 一次送模型的摘录合计上限 |

## 4. 接口一览（前缀 /api/v1）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /materials | 新增材料（201） |
| GET | /materials | 列表（topic / q / 游标分页） |
| GET | /materials/topics | 主题聚合 |
| POST | /materials/batch | 批量取 |
| POST | /materials/extract | 上传文件转 Markdown（不落库） |
| POST | /materials/fetch | 尝试抓取网页正文，用户确认后保存 |
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
| POST | /plans/{id}/resume | 载入保存时的选材、条件和方案版本，以此继续生成草稿 |
| GET | /plans | 我的方案列表 |
| GET/PATCH/DELETE | /plans/{id} | 打开 / 改标题（version）/ 删除 |
| POST | /plans/{id}/copy | 返回已存公开正文，供同一份预览和复制使用 |
| GET | /citations/{cid} | 出处展开（摘录快照 + 文件/页码 + 可跳转 URL） |
| GET | /generations/{gid}/citations | 某次生成的全部出处 |
| GET | /plans/{pid}/citations | 某份方案的出处 |
| GET | /models | 可选模型目录（不含密钥） |
| POST | /auth/register /login /logout | 注册、登录和退出。收藏、对话与方案需要登录身份 |
| GET | /auth/me | 当前身份 |

错误体统一为 `{"error":{"code":"...","message":"..."}}`；常见码：VALIDATION_ERROR / NOT_FOUND / VERSION_CONFLICT / CONFLICT_REQUIREMENTS / SOURCE_INSUFFICIENT / STALE_GENERATION / RATE_LIMITED / TRIAL_EXPIRED(_READONLY)。

## 5. 上传文件：先转 Markdown 再给模型

- `POST /materials/extract`（multipart `file`，可选 `page_start`/`page_end`）把文件转成 Markdown 文本，**不落库**，前端拿回来填进材料表单的「摘录原文」。
- 支持带文字的 PDF、DOCX、PPTX 及文本文件。单个上传文件最大 20 MB，PDF 和 PPTX 最多处理 300 页，正文最多保留 60000 字。
- 多页时每页前置 `<!-- page N -->` 标记，保证生成方案时出处页码可回溯。
- 只有链接时可以先存网址，补齐摘录后再作为引用依据。送模型的材料摘录合计默认上限为 40000 字。
- 不做 OCR、不还原版式、不解析整份 PPT/PDF 的版面结构；图片型文件会返回「没有可提取的文字」。
- 文件来源与页标随摘录保存，用户确认后才加入自己的收藏。

## 6. 生成与复核

从[对话入口](app/routers/conversations.py)开始，首轮目标和后续修改共用[条件解析](app/services/turns.py)。[生成服务](app/services/generate.py)传入本次选择的材料、必要备注、用户条件及继续修改所需的上一版方案，关闭联网搜索。单次生成的引用使用当时的原文和页码。

模型给出的候选进入[场景复核](app/services/validation.py)。页面显示最终结果与上一版的差异，保存时按[公开正文格式](app/services/plan_text.py)保留时间、物料、费用和待确认信息。[方案接口](app/routers/plans.py)负责保存、恢复和公开预览。

| 校验 | 做什么 | 失败怎么处置 |
| --- | --- | --- |
| STRUCTURE | 必要字段齐全 | block（不返回正文） |
| CITATION_GATE | 核对本次选材、引文和引文所在页 | 单项缺少有效引文时移入待确认，全部缺失时停止生成 |
| NUMBER_WHITELIST | 核对说明里的数字是否出现在材料、目标或条件中 | 未获依据的数字在正文标为待确认 |
| DURATION_TOTAL | 比较总时长、可用时间与明确保留的时长 | 调整可变环节。必留环节已超时时报告差额和取舍 |
| MATERIAL_DERIVE | 按活动环节、人数和物料依据确定数量 | 删掉环节时同步清理配套物料，并记录改动 |
| BUDGET | 费用加总、人均 vs 预算 | notice + 差额 + 放宽选项；缺单价记 pending_facts |
| TIMELINE | 用开始、截止时间及往返路程推算可停留的时间 | 重算各站到离时间，路程未知时提示补充 |
| RETAINED_COVERED | 核对已确认保留的环节和明确指定的分钟数 | 缺少必留环节时阻止该方案保存 |

引用匹配做了 Markdown 归一化（剥 `# | ** <br> <!-- page N -->` 等标记），所以材料是 Markdown 也不会误杀。

## 6.5 自然语言追问（PRD 5.2「可以追问」）

`POST /conversations/{id}/turns` 接受 `text`、可选的 `version`、`regenerate`、`model_ids` 和当前查看的 `base_generation_id`。更新条件后，对话版本递增，旧的生成作废；需要重排时返回新生成的编号。

- 能算成数字的必须落进结构化条件，才能继续走程序复核：`时间只剩 5 分钟了` → `duration_minutes=5`；`人均预算 60` → `budget_per_person=60`；`总共 300 块，20 人` → `budget_per_person=15`；`删掉猜词` → 从 `items` 里移除（连带物料由 MATERIAL_DERIVE 处理）；`15:15 出发` → `start_time`。
- 算不出数字的原句进 `conditions.user_notes`，随条件一起交给模型（最多 10 条），不影响复核数字。
- 保留要求写入 `retained.must_keep_texts`，指定的环节时长写入 `retained.fixed_item_minutes`。明确删除的环节写入 `conditions.excluded_items`，后续重排继续遵守。
- 解析结果回给前端 `{applied:[{field,from,to,label}], unresolved:[...], summary}`，前端显示「已应用/未识别」。

## 7. 模型目录

`GET /models` 返回：deepseek/deepseek-chat、kimi/kimi-k2.6、kimi/kimi-k3、qwen/qwen-plus、qwen/qwen4-max、minimax/MiniMax-M1。
前端可多选对比；`POST /conversations/{id}/generations` 带 `model_ids`，每个模型一条生成记录，各跑一次完整复核。
内部 `stub/local` 不对外暴露，仅验收脚本 `scripts/ac_acceptance.py` 用（`setdefault("model_ids", ["stub/local"])`），保证 63 项断言不依赖外部模型。

## 8. 身份与数据归属

- 注册时创建账号及数据归属身份。登录后通过 HttpOnly 会话 cookie 访问本人的材料、对话与方案。跨设备回看需要使用同一账号连接同一台服务。
- 所有资源带 `owner_id`，跨身份读写一律 404（不泄露存在性）。

## 9. 测试与验收

```bash
cd 后端
python -m pytest -q
```

网页试用从 [PRD §4.3 的三个场景](../交付物/PRD_v0.2.5.md)开始，输入原始资料和自然语言条件。旧版半验收脚本使用匿名客户端与固定模型输入。

## 10. 已知限制

- `/internal/dev/*` 三个调试端点无鉴权（造 pending 生成、注入 result、改试用期），上线前必须摘掉。
- 限流是单进程内存计数，多进程/多实例下不准。
- 生成是进程内 asyncio 后台任务，API 进程重启会丢失进行中的生成（状态停在 running）。
- 不做整份 PPT/PDF 版面解析、OCR、语音、视频、跨平台同步、实时地图订位、多人协作、并排版本对比。

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
