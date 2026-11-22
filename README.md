# 去做 · 把材料变成能执行的安排

一个帮学生把「收藏的资料」变成「可执行的安排」的产品。选定材料 → 和它对话 → 生成带出处的方案 → 存成自己的方案。

当前状态：**后端已完成并验收（51/51 通过），前端 UI 设计稿已完成，正在接入真实接口。**

---

## 目录结构

```
去做/
├── 后端/                  Python + FastAPI + PostgreSQL
│   ├── app/               应用源码
│   │   ├── routers/       路由：auth / materials / conversations / plans / models / citations
│   │   ├── services/      模型提供商、生成编排、溯源
│   │   ├── models.py      SQLAlchemy 表结构
│   │   ├── schemas.py     Pydantic 请求/响应契约
│   │   └── main.py        入口（含 /app 静态前端挂载）
│   ├── tests/             自动化测试
│   ├── scripts/           辅助脚本
│   ├── web/               前端（纯静态、无构建，由后端 /app 挂载）
│   ├── requirements.txt
│   └── .env.example       环境变量模板（真实 .env 不入库）
├── 交付物/                 设计稿、PRD、验收报告
├── 脚本/                   一次性工具脚本
└── 过程文件/               留痕日志
```

---

## 快速开始（后端）

前置：Python 3.11+、PostgreSQL 14+

```bash
cd 后端
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # 然后填你自己的 key
```

Database 配置`：`QU_DATABASE_URL=postgresql+psycopg://用户:密码@127.0.0.1:5432/库名`

建表是启动时自动完成的（`app/main.py` 里的 `Base.metadata.create_all`），不需要迁移脚本。

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
| `QU_DATABASE_URL` | PostgreSQL 连接串 |
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

- **多模型对比**：一次生成可同时调最多 4 个模型，并排比较结果
- **溯源**：方案正文里的每条结论都能回溯到来源材料的页码
- **两种材料状态**：`citable`（可引用）/ `link_only`（仅跳转，自动排除在生成之外）
- **匿名试用**：未注册用户自动获得 30 天试用身份，登录后转为正式账号
- **文件转 Markdown**：上传 PDF/Word/PPT 先解析成 Markdown 再入库，保证模型能读

---

## 团队协作

- 主分支 `main`，改动走分支 + Pull Request
- 提交信息用中文，一句话说清改了什么
- 不要提交 `.env`、数据库文件、`pgsql17_root/`（已在 `.gitignore` 排除）

---

## 文档

| 文档 | 位置 |
|---|---|
| 产品需求 | `交付物/PRD_v0.2.4.md` |
| 前端设计说明 | `交付物/前端设计_2026-11-22/README_接口映射与设计说明.md` |
| 后端验收报告 | `交付物/去做_后端实现与验收报告_2026-11-21.md` |
