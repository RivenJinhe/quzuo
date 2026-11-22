# 去做 · 前端 UI 设计原型 v0.1

日期：2026-11-22　｜　对应后端：qu-backend-20261121（未改动任何后端文件）
依据：PRD v0.2.4（5.2 页面与流程）+ 后端 app/schemas.py、app/routers/* 实际契约

## 1. 文件

| 文件 | 说明 |
| --- | --- |
| `去做_前端UI设计原型_v0.1.html` | 单文件可交互原型，双击用 Chrome/Edge 打开即可点。内联 CSS + 原生 JS，无构建、无框架、无网络依赖（除 Google Fonts，离线时自动回退系统字体）。 |
| `design-tokens.css` | 原型外观依赖的 token 文件，必须与 html 同目录。 |

原型用来定版式、文案、状态与交互路径；不是可发布代码，未接任何真实请求。

## 2. 屏幕地图

左侧导航 `data-goto` 切屏，也支持 URL hash（`#screen-collect` 等）。

| 屏 id | 名称 | PRD | 核心职责 |
| --- | --- | --- | --- |
| screen-collect | 我的收藏 | 5.2.1 | 材料卡网格、搜索、主题筛选、勾选送入对话、材料抽屉（网页/上传/手打）、卡片 ⋯ 菜单 |
| screen-arrange | 做个安排 | 5.2.2 | 左对话流四步（说任务→确认材料→生成→改与存）+ 右结果栏（模型切换、条目、时间轴、复核、保存） |
| screen-plans | 我的方案 | 5.2.3 | 方案卡列表，⋯ 菜单：重命名/复制/再改条件/删除 |
| screen-detail | 方案详情 | 5.2.3 | 公开正文 + 私人备注（琥珀，标注不随公开版复制）+ 条件 chips + 选择理由 + 右栏出处快照 |
| screen-auth | 登录/注册 | 附录 A | 试用与账号语义说明，登录/注册双 tab |
| screen-mobile | 手机端 | 5.3 | 三屏 300px 展示：收藏 / 对话 / 方案 |

## 3. 交互 → 接口映射

后端约定：无前缀（`/materials`、`/conversations`…），JSON camelCase 出入参，错误体 `{error:{code,message,field}}`。

### 收藏页

| 前端动作 | 接口 |
| --- | --- |
| 首屏/加载更多 | `GET /materials?topic=&q=&cursor=&limit=20` → `{items,nextCursor}`（后端固定按 updated_at 倒序） |
| 主题 chips 及计数 | `GET /materials/topics` → `{items:[{topic,count}]}` |
| 搜索框输入 | 同上 `GET /materials?q=`（title/excerpt/note 模糊） |
| 抽屉保存（新建） | `POST /materials`：title、topic(自由文本 1–40)、excerpt、sourceKind、sourceUrl、sourceFile、sourcePageStart/End、note、status |
| 抽屉保存（编辑） | `PATCH /materials/{id}` |
| 卡片 ⋯ 删除 | `DELETE /materials/{id}`（软删，返回 204） |
| 上传文件取正文 | `POST /materials/extract`（multipart `file` + `page_start`/`page_end`，≤20MB，txt/md/pdf/docx/pptx，只转纯文本、不落库，用户确认后才存成材料） |
| 勾选 N 份送入对话 | `POST /conversations` 拿到 cid，再 `POST /conversations/{cid}/materials/confirm`：`{materials:[{id, use, reason}]}`（≤20） |

`use:false` 就是设计稿里「仅跳转 · 自动排除」的开关语义，`reason` 存排除理由。

### 对话页

| 前端动作 | 接口 |
| --- | --- |
| 进入时取回上下文 | `GET /conversations/{cid}`（含 turns、materials、conditions、version） |
| 展开「检索命中」 | `GET /conversations/{cid}/retrieve` |
| 改材料开关 | `POST /conversations/{cid}/materials/confirm`（重新提交整组） |
| 条件 chips 增删 | `PATCH /conversations/{cid}/conditions`：`{version, conditions, retained}`（version 用于乐观锁，冲突返回 409） |
| 一句话追问 | `POST /conversations/{cid}/turns`：`{text(≤500), version, regenerate, modelIds}` → 202 |
| 点「生成」 | `POST /conversations/{cid}/generations`：`{materialIds≤20, conditions, retained, pendingFacts, modelIds≤8}` → 202 |
| 骨架屏轮询 | `GET /generations/{gid}`，看 `status`（pending/running/ok/blocked/failed）与 `result/validation/blockReason/errorCode` |
| 取消生成 | `POST /generations/{gid}/cancel` |
| 模型下拉 | `GET /models` → deepseek-chat（默认）/ kimi-k2.6 / kimi-k3；对比上限 `llm_max_compare`（默认 4） |
| 保存为方案 | `POST /plans`：conversationId、generationId、title、body、bodyPrivateNote、rationale、confirmedConditions |

设计稿里三种状态都画了：正常结果、`blocked`（SOURCE_INSUFFICIENT，补材料/看出处）、超时降级提示（30s 未返回自动退回 DeepSeek Chat）。`pendingFacts` 对应右上角「2 处需要你确认」的 notice。

### 方案页

| 前端动作 | 接口 |
| --- | --- |
| 列表 | `GET /plans`（卡片：标题、任务类型、条件摘要、时间、材料数） |
| 详情 | `GET /plans/{id}` |
| 重命名 / 再改条件 | `PATCH /plans/{id}` |
| 复制公开版 | `POST /plans/{id}/copy` → 返回只含公开正文的新方案（私人备注、内部 rationale 不复制） |
| 删除 | `DELETE /plans/{id}` |
| 出处快照 | `GET /plans/{pid}/citations`；对话内用 `GET /conversations/{cid}/citations`、单条用 `GET /citations/{cid}` |

### 账号

`POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me`（cookie 会话）。

试用语义按后端实现（app/routers/auth.py `_extend_trial`）：匿名试用 30 天，到期转只读，再过 7 天清理；**注册或登录后不再计时**。原模板里「注册后自动开 30 天试用」的说法与后端矛盾，已按实现改写。

## 4. 界面上写死的硬规则

1. 仅跳转材料（`use:false`）勾了也不进生成，卡片上标明「自动排除」。
2. 生成中可取消，30s 无返回自动换模型并留下一条降级说明，不静默重试。
3. 被拦下（`blocked`）必须给出错误码与补救入口，不给「重新生成」这种空转按钮。
4. 私人备注只在本机详情页可见，复制公开版时明确提示不带走。
5. 对比模式下模型数不超过 `llm_max_compare`，超出置灰。
6. 版本冲突（409）不覆盖用户已改的条件，提示「另一处已改，刷新看最新」。
7. 上传件只进不出：抽出的 Markdown 需用户确认后才成为材料。

## 5. 视觉规范

| token | 值 | 用途 |
| --- | --- | --- |
| --canvas / --surface | #F4F3EE / #FFFFFF | 纸感底色 / 卡片 |
| --ink / --muted / --faint | #15160F / #6D6F63 / #9A9C8F | 三级文字 |
| --line | #E2E0D6 | 1px 分隔线 |
| --accent / --accent-2 / --accent-soft | #1F4D3F / #2F6A57 / #E8F0EA | 主色（深墨绿），用于主按钮、命中、选中 |
| --amber / --amber-soft | #9A6A15 / #F7EEDC | 私人备注、待确认、警示 |
| --danger | #9C3B2E | 删除、拦截 |
| 圆角 | 20 / 14 / 10 px | 卡片 / 控件 / 小标签 |
| 字体 | Plus Jakarta Sans + JetBrains Mono（数字、时间、页码），中文回退系统字 | 正文 14.5px / 行高 1.65 |

布局：侧栏 256px（≤1080px 收成 76px 图标栏，≤720px 隐藏并出底部 tabbar）；收藏页网格 3 列，≤1080px 2 列；对话页左流右栏（结果栏 380px），≤1080px 堆叠。

## 6. 已知限制

- 未接真实接口，所有数据为演示常量（微积分复习 / 周末返校 / 社团招新三个场景）。
- 图标为内联 SVG 几何图形，无图标库依赖；无暗色模式。
- 本机 Chrome/Edge 在沙箱内起不来（mojo/crashpad 拒权），渲染只在 DSH Design 面板预览和人眼确认，没有自动化截图回归。
- 移动端只做了展示屏，未做真实响应式交互实现。
