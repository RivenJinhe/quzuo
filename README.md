---
title: 去做
emoji: 🗂️
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# 去做 · 把材料变成能执行的安排

一个帮学生把「收藏的资料」变成「可执行的安排」的产品。选定材料 → 和它对话 → 生成带出处的方案 → 存成自己的方案。

首版按 [PRD v0.2.5](交付物/PRD_v0.2.5.md) 开发。注册或登录后，从产品入口 `/app/` 进入“我的收藏”“做个安排”和“我的方案”。三个页面由 [React 应用](前端/src/App.tsx) 组织。

`/` 和旧链接 `/app/design.html` 都指向同一产品入口。开发联调用的[接口测试台](前端/src/testbench/main.tsx) 位于 `/app/testbench.html`。

## 首版试用范围

同学从收藏录入材料和备注，在助手里核对选材，再按实际条件生成和修改安排。改条件先生成草稿，点击保存才更新“我的方案”里的版本。公开预览和复制使用同一份正文。

在桌面浏览器登录后，可以拖入一个文件或网址，核对摘录后保存到“我的收藏”。支持可直接下载的公开文件链接。也可以在“新增材料”里选择文件或粘贴网址。需要登录才能下载的文件可先下载再导入。

| 场景 | 试用时改变的条件 | 预期结果 |
|---|---|---|
| 复习 | 存机会成本讲义第 8 页与例题第 12 页，备注写明例题的困惑。把 15 分钟缩到 5 分钟并保留例题。 | 从排练和兼职的取舍讲起，两处能查看原文和页码。练题与复述由 10＋5 分钟改成 4＋1 分钟。 |
| 出行 | 15:15 离馆，两段路各 15 分钟，须在 15:55 前返校。想喝半小时咖啡，已知人均花费 95 元，预算 80 元。 | 15:30 到店、15:40 离店，最多停留 10 分钟。停留半小时会晚 20 分钟。人均超出 15 元，取舍基于材料中的已知费用。 |
| 活动 | 20 人迎新、桌椅固定，40 分钟缩到 25 分钟，保留 12 分钟同桌互访并删掉猜词。再改成 10 分钟。 | 环节适合固定座位。25 分钟可分为 2＋12＋8＋3，题卡由 20 张变为 0 张。缩到 10 分钟时提示必留环节超出 2 分钟。 |

三种方案都应支持刷新后回看、按保存版本继续修改，并保留时间、费用或物料。试用安排与设备范围见 [PRD §4.3、§6.2、§8.1](交付物/PRD_v0.2.5.md)。对外试用使用 HTTPS，访问说明应写明网址、开放时段和数据保存位置。

### 下一阶段

1. **准备试用环境。** 将统一入口、生成服务和持久化数据库部署到同一套试用环境，配置至少一个真实模型，提供同学可直接打开的网址。
2. **小范围试用。** 复习、出行、活动各安排 3 名同学。每个场景至少 2 人能独立完成并指出自己的哪条要求改变了方案，作为首版交付目标。
3. **按试用结果修正。** 优先修复妨碍选材、生成、改条件、保存或回看的问题。图片识字、实时地图和多人协作留在首版之后。

---

## 目录结构

```
去做/
├── 前端/                  pnpm + React + TypeScript，Vite 构建产品页与接口测试台
├── 后端/                  Python + FastAPI，支持 SQLite 和 PostgreSQL
│   ├── app/               应用源码
│   │   ├── routers/       路由：auth / materials / conversations / plans / models / citations
│   │   ├── services/      模型提供商、生成编排、溯源
│   │   ├── models.py      SQLAlchemy 表结构
│   │   ├── schemas.py     Pydantic 请求/响应契约
│   │   └── main.py        入口（含 /app 静态前端挂载）
│   ├── tests/             自动化测试
│   ├── scripts/           辅助脚本
│   ├── web/dist/          前端生产构建产物，由 FastAPI 挂载到 /app
│   ├── requirements.txt
│   └── .env.example       环境变量模板（真实 .env 不入库）
├── 交付物/                 设计稿、PRD、验收报告
├── 脚本/                   一次性工具脚本
└── 过程文件/               留痕日志
```

---

## 前端开发与构建

使用 Node.js 24 和项目 [package.json](前端/package.json) 指定的 pnpm 版本。在仓库根目录执行：

```bash
pnpm --dir 前端 install --frozen-lockfile
pnpm --dir 前端 dev
```

启动下方的 FastAPI 服务后，打开 `http://localhost:5173/app/` 开发产品页，或打开 `http://localhost:5173/app/testbench.html` 调试接口。Vite 将 API 请求代理到 `127.0.0.1:8010`，两个页面共用登录会话。

从 [React 入口](前端/src/main.tsx) 进入[收藏](前端/src/materials/)、[安排](前端/src/arrange/)和[方案](前端/src/plans/)组件。产品页与[测试台](前端/src/testbench/)共用[接口类型](前端/src/types.ts)、[请求和生成轮询](前端/src/api.ts)。

生产构建运行 `pnpm --dir 前端 build`。通过 TypeScript 检查后，Vite 将两个 HTML 入口和静态资源写入 `后端/web/dist/`。先完成构建再启动 FastAPI，即可从后端端口访问页面。仓库 Dockerfile 会在镜像构建时执行这一步。

## 快速开始（后端）

Windows 用户可在仓库根目录打开 PowerShell，使用 Python 3.11+ 启动本地应用。首次从源码构建页面时，还需要上文所列的 Node.js 和 pnpm。

```powershell
.\start.ps1
```

脚本会准备后端虚拟环境，首次从样例创建 `后端/.env`，默认使用 SQLite。已有配置会继续生效。前端构建产物存在时直接复用，修改页面后可运行 `pnpm --dir 前端 build` 更新。打开 `http://127.0.0.1:8010/app/` 即可使用，按 Ctrl+C 停止。要更换端口，可执行 `.\start.ps1 -Port 8020`。

手动启动时，在仓库根目录执行以下命令。

```bash
cd 后端
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

在 PowerShell 中，使用 `.\.venv\Scripts\Activate.ps1` 激活虚拟环境，使用 `Copy-Item .env.example .env` 复制配置。已有 `.env` 时，编辑其中的 `QU_DATABASE_URL` 即可切换数据库。

复制的配置使用 `QU_DATABASE_URL=sqlite:///./qu_dev.sqlite3`。从 `后端` 目录启动后，应用会创建 `后端/qu_dev.sqlite3` 并初始化表。若使用 PostgreSQL，请先创建数据库，再按[后端说明](后端/README.md#使用-postgresql)设置连接串。

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

- 接口文档：http://127.0.0.1:8010/docs
- 前端页面：http://127.0.0.1:8010/app/

---

## 环境变量

见 `后端/.env.example`。需要配置：

| 变量 | 说明 |
|---|---|
| `QU_DATABASE_URL` | SQLite 或 PostgreSQL 连接串，本地样例使用 SQLite 文件 |
| `QU_PROVIDER_DEEPSEEK_KEY` | DeepSeek API Key |
| `QU_PROVIDER_KIMI_KEY` | Kimi / Moonshot API Key |
| `QU_PROVIDER_QWEN_KEY` | 通义千问 API Key |
| `QU_PROVIDER_MINIMAX_KEY` | MiniMax API Key |
| `QU_LLM_ALLOW_REAL` | 是否真调模型（`true`/`false`） |
| `QU_LLM_DEFAULT_MODEL` | 默认模型，如 `deepseek/deepseek-chat` |
| `QU_LLM_TIMEOUT_SECONDS` | 单次生成超时秒数 |

> ⚠️ **`.env` 已在 `.gitignore` 中，不会提交。每个开发者用自己的 key。**

---

## 技术要点

- **模型选择**　产品页从服务端读取可用模型。试用环境须开启真实模型并配置至少一个提供商的密钥。选择同时请求多个模型时，页面展示首个模型的结果。
- **溯源**：生成服务校对引用摘录，保存材料标题、文件名、页码或网页链接的快照，供回看出处时使用
- **材料状态**：`citable`（可引用）、`link_only`（仅链接）、`unusable`（不可用）。选入后两类材料时，生成接口提示补正文或调整选材
- **账号**　使用收藏、对话和方案前需注册或登录，材料和方案按账号存取。
- **文件导入**　上传带文字的 PDF、DOCX 或 PPTX 后，可预览并编辑提取的正文，点击保存后加入收藏。

---

## 团队协作

- 主分支 `main`，改动走分支 + Pull Request
- 提交信息用中文，一句话说清改了什么
- 不要提交 `.env`、数据库文件、`pgsql17_root/`（已在 `.gitignore` 排除）

---

## 文档

| 文档 | 位置 |
|---|---|
| 产品需求 | [PRD v0.2.5 原件](交付物/PRD_v0.2.5.docx) · [Markdown 版](交付物/PRD_v0.2.5.md) |
| 上一版产品需求 | [PRD v0.2.4](交付物/PRD_v0.2.4.md) |
| 前端设计说明 | `交付物/前端设计_2026-11-22/README_接口映射与设计说明.md` |
| 后端验收报告 | `交付物/去做_后端实现与验收报告_2026-11-21.md` |
