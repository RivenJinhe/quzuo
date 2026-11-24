# 去做 · 前端 UI 设计原型 v0.2

日期：2026-11-23　｜　对应后端：已改为「必须注册才能用」（app/deps.py 强制登录态），纯后端 zip 未改动
依据：PRD v0.2.4（5.2 页面与流程）+ 后端 app/schemas.py、app/routers/* 实际契约

## 1. 文件

| 文件 | 说明 |
| --- | --- |
| `去做_前端UI设计原型_v0.2.html` | 单文件可交互原型，双击用 Chrome/Edge 打开即可点。内联 CSS + 原生 JS，无构建、无框架、无网络依赖（除 Google Fonts，离线时自动回退系统字体）。 |
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
| screen-auth | 登录/注册 | 附录 A | **初始屏**（登录墙）。未登录时任何导航都被拦回这里；登录/注册双 tab |
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

**必须注册或登录才能使用**（后端已改，app/deps.py 的 `get_trial` 不再自动建匿名身份：无 cookie → 401 UNAUTHENTICATED「请先注册或登录」；token 失配或 `account_id` 为空 → 401「登录状态已失效，请重新登录」）。原「匿名试用 30 天」的语义已废弃，`POST /auth/register` 现在直接新建有效期 36500 天的身份并下发会话 cookie。`GET /auth/me` 未登录返回 `{"logged_in":false,"account":null,"trial":null}`，可当登录态探针用。

前端对应实现：`go(id)` 顶部拦未登录跳转，`submitAuth()` 做本地校验后 `signIn()` 切换 UI；接真接口时把 `signIn(name)` 换成 `POST /auth/register` / `POST /auth/login` 的响应处理即可，其余屏幕不用改。

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

- 未接真实接口。v0.2 起**空库起步，无任何预置数据**（原微积分复习 / 周末返校 / 社团招新三个演示场景已全部删除），各屏展示的是真实空态。
- 图标为内联 SVG 几何图形，无图标库依赖；无暗色模式。
- 本机 Chrome/Edge 在沙箱内起不来（mojo/crashpad 拒权），渲染只在 DSH Design 面板预览和人眼确认，没有自动化截图回归。
- 移动端只做了展示屏，未做真实响应式交互实现。

## 7. v0.2 变更（2026-11-23）

配合后端「必须注册才能用」的改造，前端同步做了两件事。

**一、加登录墙**

- 初始屏改为 `screen-auth`（`go(loggedIn ? 'collect' : 'auth')`）。
- `go(id)` 顶部加门禁：`if (!loggedIn && id !== 'auth') { toast('请先注册或登录'); id = 'auth' }`。
- 删除原「先不登录，用试用身份看看」按钮。
- `submitAuth()` 只做本地校验（用户名 ≥3 位、密码 ≥8 位），通过后 `signIn(u)` 切换侧栏账号区、显示退出按钮、进收藏页。
- 退出登录：`logout()` 复位 `loggedIn` 并回到 auth 屏。

**二、清空全部 demo 预置内容**

| 位置 | 原内容 | 现状态 |
| --- | --- | --- |
| 侧栏 nav 计数 | 全部 6 / 3 / 1 / 1 | 全部 0 |
| 侧栏试用块 | 「匿名试用 30 天」提示 | 整块删除 |
| 侧栏账号区 | 匿名试用身份 | `#accountName`「未登录」+ `#accountSub`「注册后开始使用」+`#accountAvatar`「未」，`#logoutBtn` 隐藏 |
| 收藏屏 | 6 张真实材料卡（课件第 8–11 页、导师备注、自习室预约等） | `#matGrid` 清空 + `#emptyFirst`「还没有材料」 |
| 安排屏 | 「对话 #A7F2」+ 完整四步对话流 + 结果栏 4 条条目/时间轴/八项复核 | 删 badge、steps 全 pending、`#threadEmpty`「说一句要安排什么」+ 三个引导按钮、右栏空态 |
| 方案屏 | 3 张方案卡（复习/出行/活动） | `#planGrid` 清空 + `#planEmpty`「还没有方案」 |
| 方案详情 | 完整正文 + 条件 + 私人备注 + 出处快照 | 整屏空态「还没有选中的方案」+ 返回按钮 |
| 手机端 | 三屏演示内容 | 三个空态 |
| 材料抽屉 | 预填的标题/链接/页码/摘录 | 全部清空，Markdown 预览改为占位说明 |

**三、新增/改写的前端函数**

`loggedIn` / `authMode` 状态、`signIn(name)`、`updateAccount()`、`submitAuth()`、`fillComposer(txt)`、`logout()`（已存在，语义改为回 auth 屏）、`filterMats(q)`（重写：零结果显示 `#emptyFirst`，否则切换 `#emptyState` 并同步 `#gridCount` 文案）、`sendMsg()`（发送时隐藏 `#threadEmpty`）。

**四、校验**

静态检查全绿：65516 字节 / 1207 行、div 190/190、section 6/6、script 语法通过、无缺失 id、无未定义函数、初始 active 屏为 `screen-auth`、`hasTrial=false`、`hasLin=false`（旧试用文案与旧演示数据均已无残留）。
