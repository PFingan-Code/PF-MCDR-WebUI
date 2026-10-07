# GUGU WebUI — API 文档（REST 重构基线）

> **契约权威来源：本仓库以后端 OpenAPI 为准** —— 启动服务后访问
> `GET /openapi.json`（Swagger UI：`/docs`、ReDoc：`/redoc`）可导出全量路由、
> 请求/响应模型与参数约束；本文档是其按业务域的说明性配套，若与 OpenAPI 出现
> 分歧，一律以 OpenAPI 为准。
>
> 所有 `/api/*` 成功端点（除图标/文件等二进制响应外）在 OpenAPI 中均声明统一
> `response_model`（`ApiSuccessEnvelope` / `PageEnvelope`），并有
> `tests/snapshots/openapi_routers.json` 快照测试守护，防止无意破坏契约。

## 通用约定

### 1. 统一响应外壳（全库唯一）

**成功（2xx）**

```json
{ "status": "success", "message": "可选提示", "data": { } }
```

- `status` 只允许 `success | error`，**绝不**用于承载业务状态。
- 业务负载一律放在 `data` 内；纯提示类响应可省略 `data`，只带 `message`。
- 业务状态改独立字段：服务器在线 → `data.online: bool`；配对状态机 →
  `data.phase: "pending|accepted|denied"`；验证码 → `data.verified: bool`。

**失败（非 2xx）**

```json
{ "status": "error", "message": "人类可读信息", "code": "机器码", "data": { } }
```

- `code` 示例：`http_401`、`validation_error`、`plugin_not_found`、
  `task_not_found`、`super_admin_required`。业务上下文放在 `data`（例如模组
  上传冲突的 `data.warnings`）。
- 参数校验失败（FastAPI 422）也走该外壳：`code = "validation_error"`，
  `data.errors` 为明细数组，不再输出裸 `{"detail": [...]}`。
- 铁律：`status: "error"` 必须配非 2xx；2xx 不携带错误语义。

### 2. 分页列表

分页响应统一为：

```json
{ "status": "success", "data": { "items": [], "total": 0, "offset": 0, "limit": 50 } }
```

当前已统一的分页端点：`GET /api/players`、`GET /api/chat/messages`、
`GET /api/mods/trash`、`GET /api/audit_logs`。其余列表（`data.plugins`、
`data.packages`、`data.pages`、`data.versions`、`data.files`、
`data.servers` 等）为一次性全量数组，仍放于 `data` 下各自的具名键。

### 3. 命名与数据格式

- 路径 `kebab-case`、查询参数 `snake_case`；资源用复数名词与层级表达从属
  （如 `GET /api/plugins/{id}/versions`）。
- 动作语义：状态切换 → `PUT .../enabled`（body `{enabled: bool}`）；一次性
  副作用命令 → `POST /api/server/commands|controls`、`POST /api/pip/tasks`
  （任务创建返回 `task_id` 后轮询子资源）。
- 时间字段（`ts`、`timestamp`、`released_at`、`last_update_time`、
  `expires_at`、`deleted_at`、`modified_at`、`start_time/end_time/access_time`、
  `last_seen`、`created_at`、`session_seconds` 等）**统一为 epoch 秒**；前端在
  `utils/format.ts` 集中格式化（`formatEpoch`/`formatEpochDate`/`formatDuration`，
  见 `pnpm test:format`）。日志行的内嵌时间字符串属于“日志内容”，不作字段契约。
- 任务状态枚举统一：`running | completed | failed`（PIM 与 pip 一致；
  无 `pending/success/error`）。
- 无意义的重复字段已移除：版本 `date/created_at/release_date` 只留
  `released_at`；在线插件的假 `update_time` 只留 `last_update_time`；
  自更新 `success` 双键、QQ 登录 `ret` 魔数、玩家 `isLocal` 驼峰等均已废除。

### 4. HTTP 状态码语义

| 场景 | 状态码 |
|---|---|
| GET / PUT / POST 成功 | 200 |
| 创建成功 / 任务创建 | 200（含 `data.task_id`）或 201 |
| 参数/业务校验失败 | 400 |
| 未登录 | 401 |
| 无权限（非管理员 / 非超管） | 403 |
| 资源不存在 | 404 |
| 依赖冲突（已存在 / 被依赖） | 409 |
| 上传超限 / 内容不支持 | 413 / 415 |
| 频率限制 | 429 |
| 服务端 / 上游错误 | 500 / 502 / 503 |
| 参数类型 / 枚举校验失败 | 422（统一错误体） |

### 5. 认证方式

1. **浏览器会话**：Cookie `token`（唯一凭证，用户名取自服务端 token 记录，
   不读取 session 中的 `username`）。token 有效期在接口层校验（默认 1 天，
   `remember` 为 365 天），过期即 401 并从库中清除。
2. **子服模式**（`panel_role: slave`）：请求头 `X-Panel-Token`，值为
   `config.json` 中 `panel_master.allowed_tokens` 已启用项的 `token`；可选配
   `allowed_master_ips` 限制来源 IP。此时用户名为 `__panel__`，管理员校验放行
   （权限由主服侧判定）。
3. **脚本/会话签名**：session cookie 由随机生成并持久化到 SQLite 的密钥签名
   （`app_state` 中 `secrets/session_secret_key`），不再使用固定密钥。

权限分三级：`登录`（`get_current_user`）、`管理员`（`get_current_admin`）、
`超级管理员`（`get_super_admin`，仅模组永久清理与上传上限修改等少数动作）。
未登录访问需登录接口 → **401**；已登录但权限不足 → **403**（非 API 页面
401/403 会重定向到登录页）。

登录类接口按失败次数限流（滑动窗口 5 分钟）：同一 IP + 账号 5 次失败，或同一
IP 合计 20 次失败后，返回 **429** `too_many_attempts`（`data.retry_after` 秒数，
表单登录响应另带 `Retry-After` 头）。成功登录清除该账号的失败计数。

### 6. 多服面板代理（主服）

`panel_role: master` 时，对**可代理**的请求可指定目标：请求头
`X-Target-Server`（值为 `panel_slaves[].id`）或查询参数 `serverId`（转发时从
出站查询剔除）。未指定或 `local` 表示本地执行。

**始终仅在主服本地处理、绝不代理**的清单（`is_proxy_candidate_path`，
由 `tests/test_proxy_local.py` 全量路由扫描守护）：

- 精确路径：`/api/login`、`/api/login/qq_qr/start`、`/api/login/qq_qr/status`、
  `/api/logout`、`/api/auth/me`、`/api/servers`、`/api/panel_merge_config`、
  `/api/audit_logs`、`/api/i18n/languages`、`/api/plugins/online`
  （含旧路径兜底 `/api/online-plugins`）
- 前缀：`/api/pairing/*`

### 7. 前端页面（非 API）

`GET /login`、`/index`、`/home`、`/mc`、`/mcdr`、`/plugins`、
`/online-plugins`、`/settings`、`/about`、`/chat`、`/player-chat`、
`/terminal`、`/operation-logs`、`/players`、`/mods` 等由服务端返回 React SPA
的 `index.html`（权限与对应 `Depends` 一致）；非 `/api/*` 的未知路径同样回退
SPA 由前端路由处理。

### 8. 操作审计

多数写操作（插件开关/重载/安装/卸载/更新、服务器控制/命令、配置保存、玩家
白名单/OP/封禁/踢出、模组操作、pip 任务、面板合并配置等）会写入审计，通过
`GET /api/audit_logs` 查询（见下文）。

---

## 认证 / 会话 / 语言

### POST /api/login — 表单登录

- 参数（`application/x-www-form-urlencoded`）：`account`、`password`，
  可选 `temp_code`（临时登录码）、`remember`。
- 属“保持现状”项（表单 + Cookie 会话为常规做法，不纳入 REST 外壳）。
- 当 `disable_other_admin` 为真时，**账号密码与临时码登录都只允许
  `super_admin_account`**（其他账号或 `tempuser` → **403**）。
- 失败次数超限 → **429** `too_many_attempts`（见“认证方式”）。
- 登录页为 `GET /login`；QQ 扫码登录：`POST /api/login/qq_qr/start`
  （返回 `code` + `qrUrl`）→ 轮询 `GET /api/login/qq_qr/status?code=...`。

### GET /api/auth/me — 当前登录用户（检查登录状态）

- 权限：登录。未登录 → **401** `http_401`。
- `data`：`{username, nickname, is_admin, is_super_admin}`（`nickname` 来自
  QQ 昵称表，无则 `null`）。
- 旧路径 `/api/checkLogin`（顶层平铺）已下线。

### POST /api/logout — API 登出

- 返回 `{"status":"success","message":"Logged out"}`。页面登出仍走
  `GET /logout`（302 重定向登录页）。

### GET /api/i18n/languages — 语言列表

- 无需登录。`data.items`：`[{code, name}, ...]`。
- 旧路径 `/api/langs`（裸数组）已下线。前端当前无消费方，为可保留的过渡接口。

---

## 服务器域

### GET /api/server/status — 服务器状态

- 权限：WebUI 登录态，或公开聊天页的有效会话（query `session_id`）。
  `data = {online: bool, version: string, players: string}`。
- 旧 `/api/get_server_status`（顶层 `status: "online|offline"` 双义）已下线。

### POST /api/server/controls — 启停控制

- 权限：管理员。body `{"action": "start"|"stop"|"restart"}`。
- 非法动作 → **400** `invalid_action`；执行失败 → **400** `control_failed`。
- 旧 `/api/control_server` 已下线。

### POST /api/server/commands — 发送命令

- 权限：管理员。body `{"command": "..."}`（≤2000 字符；空 → **400**
  `invalid_command`）。
- `data` 可选：`{feedback, capture?, timed_out?, note?}`，`message` 为提示。
- 语义：`!` 开头为 MCDR 命令（捕获源权限 4 直接执行）；其余为游戏命令 ——
  RCON 已连接时优先 RCON 并回传直接反馈，否则「直接执行 + 输出捕获」，
  **均不需要 RCON**。`feedback` 可能为空（无回显）。
- 保护：禁止 `!!MCDR plugin reload/unload/disable guguwebui`（含 `!!mcdr` 与
  `plg` 别名、大小写与多余空格变体）与服务器停机命令 `stop`（含 `/stop`）
  → **403** `forbidden_command`；执行失败 → **400** `command_failed`。
- 旧 `/api/send_command` 已下线。

### GET /api/server/command-suggestions — 命令补全

- 权限：登录。query `input`（可选，≤200）。`data.suggestions`：
  `[{command, description}]`。

### GET /api/server/logs — 日志（快照 + 增量统一）

- 权限：登录。query：`cursor`（0=尾部快照；>0=该 counter 之后增量）、
  `max_lines`（1–500，默认 100）。
- `data = {logs: [{line_number, content, source: "mcdr"|"minecraft", counter}],
  total_lines, next_cursor, new_logs_count}`。
- 旧 `/api/server_logs`、`/api/new_logs`（含 `start_line`）已下线合并。

### GET /api/server/rcon-status — RCON 状态

- 权限：登录。`data = {rcon_enabled, rcon_connected, rcon_info?}`
  （`rcon_info` 含 `list_response` / `player_info` / `error`）。

### POST /api/server/rcon-setup — 一键启用 RCON

- 权限：管理员。写入 RCON 配置并尝试连接；**不回传密码**（安全要求），
  `data` 仅含 `{rcon_host, rcon_port}` 等非敏感项。
- 旧 `/api/setup_rcon` 已下线。

### 监控（MonitorService，权限：登录）

- `GET /api/monitor/overview`：最新快照。`data = {ts, online, uptime, tps,
  mspt, cpu:{system,minecraft}, memory:{total,available,used,percent,minecraft,
  swap_total,swap_used,swap_percent}, disk:{path,total,used,percent},
  load:{load1,load5,load15}, network:{rx,tx}}`。`ts/uptime` 为 epoch 秒。
  TPS/MSPT 经 RCON 采样（每秒记录；RCON 不可用或命令不支持时为 `null`）。
- `GET /api/monitor/history?metric=cpu&range=1h`：时间序列。`metric ∈
  cpu|memory|network|tps|mspt|load|disk`，`range ∈ 10m|30m|1h|6h|12h|1d|3d|7d`
  （非法值 422 `validation_error`）。`data = {metric, range, sample, points:
  [{t(epoch 秒), ...}]}`（≤1h 秒级采样、更久 1 分钟均值；服务端降采样 ≤1500 点）。
- `GET /api/monitor/table?range=1h`：统计表。`data = {range, stats: {指标:
  {avg, min, max}}}`。
- 监控服务缺失时 **503** `monitor_unavailable`。

---

## 配置域

### GET /api/plugins/{plugin_id}/config-files — 插件配置文件列表

- 权限：管理员（列表内容可指向敏感配置文件）。`data.files`：
  `[{path, name, has_web}]`。
- 旧 `/api/list_config_files` 已下线。

### GET /api/config-files — 加载配置文件

- 权限：管理员（可读目录包含 WebUI 自身 `config.json`，其中有 AI Key、
  面板 token、SSL 密钥口令等敏感字段）。query：`path`（必填；受 SafePath
  约束，含 `./`、`..` 与嵌套斜杠，故放查询参数而非路径段）、`translation`、
  `type`（auto/json/yml/yaml/properties/html）。
- `data = {path, type, content, config_data?}`；缺文件 → **404**
  `config_file_not_found`（不再返回 `{}`）；越权路径 → **403`
  `path_not_allowed`。
- 旧 `/api/load_config`、`/api/load_config_file`（含任意路径 raw 版）已下线。

### PUT /api/config-files — 保存配置文件

- 权限：管理员。query `path` 同 GET。body 二选一：`content`（原文，旧
  save_config_file 语义）或 `config_data`（结构化，旧 save_config 语义）；
  同时/都不给 → **400** `invalid_body`。
- 保护文件（WebUI 自身 config.json）→ **403** `protected_file`；缺文件 →
  **404** `config_file_not_found`（旧 `"fail"` 状态值已废除）。

### GET/PUT /api/web-config — WebUI 配置

- GET 权限：登录；PUT 权限：管理员。PUT body 为结构化字段（`WebConfigSaveRequest`，
  未给字段不修改，无 `action` 分派），覆盖 host/port/super_account、
  ssl_*、ai_*、public_chat_*、chat_*、icp_records、panel_* 等。
- GET 的 `data.ai_api_key` 恒为空串，以 `ai_api_key_configured` 判断是否已配置。
- 旧 `/api/get_web_config`、`/api/save_web_config`（action 翻转）已下线。

### GET /api/config/icp-records — ICP 备案

- 公开。`data.icp_records`：`[{icp, url}]`。

### GET/PUT /api/custom-assets/{kind} — 自定义 CSS/JS

- `kind ∈ css|js`。GET 权限：登录 → `data.content`；PUT 权限：管理员，body
  `{content}`。
- 旧 `/api/load_file`、`/api/save_file` 已下线。

---

## 插件域

### GET /api/plugins — 已安装插件列表

- 权限：登录。`data.plugins`：完整元数据数组（含 id/name/version/
  status/author/link/dependencies/repository/图标等，由
  `PluginService.get_plugins_list` 提供）。
- 单项请用子资源（见下），**无 `plugin_id` 查询参数**。

### GET /api/plugins/{plugin_id} — 单个插件

- 权限：WebUI 登录态，或公开聊天页的有效会话（query `session_id`）。
  `data.plugin`；不存在 → **404** `plugin_not_found`。

### GET /api/plugins/online — 在线插件目录

- 权限：管理员。query `repo_url`（可选）。`data.items` 为目录数组
  （字段：id/name/version/description/authors/dependencies/labels/
  repository_url/license/downloads/readme_url/`last_update_time`（epoch 秒
  或 null）等）。
- 恒走主服本地（不代理）。旧 `/api/online-plugins`（裸数组）已下线。

### GET /api/plugins/web-pages — 注册的插件网页

- 权限：登录。`data.pages`：`[{id, path, name, icon}]`。旧 `/plugins/web_pages`
  已下线。
- `GET /api/plugins/web-pages/{plugin_id}/icon`：图片图标（二进制）；未配置/
  越界/缺失 → **404**。

### PUT /api/plugins/{plugin_id}/enabled — 启用/禁用

- 权限：管理员。body `{enabled: bool}`（true=加载，false=卸载）。失败 →
  **400** `plugin_action_failed`。写审计 `plugin.toggle`。
- 旧 `POST /api/toggle_plugin` 已下线。

### POST /api/plugins/{plugin_id}/reload — 重载

- 权限：管理员。失败 → **400** `plugin_action_failed`；写审计 `plugin.reload`。
- 旧 `POST /api/reload_plugin` 已下线。

### PIM 子域（版本 / 仓库 / 任务 / 安装）

- `GET /api/plugins/{plugin_id}/repository`（登录）：`data.repository`
  `{url, name, name_key, is_official}`；未收录 → **404**
  `plugin_not_in_repository`。
- `GET /api/plugins/{plugin_id}/versions`（登录；query `repo_url` 可选）：
  `data.versions`，条目统一 `{version, tag_name, prerelease, released_at(epoch
  秒), download_url, download_count, size, description}`（旧
  `date/created_at/release_date` 均已移除）。
- `POST /api/plugins/{plugin_id}/install` / `/update`（管理员；body
  `{version?, repo_url?}`）：创建安装/更新任务，`data.task_id`。
- `POST /api/plugins/{plugin_id}/uninstall`（管理员）：卸载任务，
  `data.task_id`；会联动卸载依赖方。
- 上述三类动作对 `plugin_id = "guguwebui"` 一律拒绝 → **400**
  `webui_self_operation`；失败 → **500** `pim_task_create_failed`；写审计。
- `GET /api/pim/tasks/{task_id}`（登录）：`data.task_info`；不存在 → **404**
  `task_not_found`。任务体：`{id, plugin_id, action, status:
  running|completed|failed, progress, message, start_time/end_time/access_time
  (epoch 秒), all_messages, error_messages}`；终态 30 分钟无访问回收。
- `GET /api/pim/status`（管理员）：`data = {pim_status:
  installed|not_installed, message}`。
- `POST /api/pim/bootstrap`（管理员）：把内置 PIM 打包安装为独立插件；
  失败 → **500** `pim_bootstrap_failed`。旧 `GET /install_pim_plugin`、
  `/check_pim_status`、`/pim/task_status`、`/pim/plugin_repository`、
  `/pim/plugin_versions`、`/pim/install_plugin` 等全部下线。

### 插件后端 API 代理（扩展点，保持现状）

`/api/plugin/{plugin_id}` 与 `/api/plugin/{plugin_id}/{subpath}`：第三方插件
`register_plugin_page(api_handler=...)` 注册的自定义接口。方法/入参/返回由插件
自定，**不纳入统一外壳**；`auth` 注入 `{username, auth_via, is_admin,
is_super_admin, is_panel}`；支持 multipart 文件解析；错误语义
404/415/400/413 由插件注册规则决定。

---

## 玩家域（权限：管理员）

数据源为 `whitelist.json` / `ops.json` / `banned-players.json` /
`banned-ips.json` / `usercache.json` 与 RCON 实时查询；服务器离线时文件类操作
退化为直接改写（重启生效，`message` 会说明）。

- `GET /api/players`：分页列表（`search`/`filter ∈ all|online|offline|bot|op`/
  `offset`/`limit ≤200`/`exclude_bots`）。`data.items`（分页统一；旧
  `data.players` 已下线）+ `total/offset/limit/online_count/bot_count/
  server_running`。条目字段：name/uuid/online/is_bot/ips/ip/is_op/whitelisted/
  banned/session_seconds/total_playtime/last_seen(epoch 秒)/position/dimension。
- `GET /api/players/bots`：`data = {bots, total, server_running}`。
- `GET /api/players/whitelist`：`data = {enabled, members:[{name,uuid}],
  server_running}`。
- `GET /api/players/ops`：`data = {ops:[{name,uuid,level?,
  bypassesPlayerLimit?}], server_running}`。
- `GET /api/players/bans`：`data = {players:[{name?,uuid?,reason?,created?,
  expires?,source?}], ips:[...], server_running}`（时间字段来自原版封禁文件，
  属文件内容）。
- 在线情况统计（会话日志驱动，数据存于 `guguwebui_static/player_stats.json`
  的 `sessions`，保留 90 天；`range ∈ 10m|30m|1h|6h|12h|1d|3d|7d`，非法值
  422 `validation_error`；`exclude_bots` 排除无 IP 玩家/假人）：
  - `GET /api/players/stats/overview?range=1h&exclude_bots=`：`data =
    {range, current_online, avg_online, peak_online, peak_ts(epoch 秒),
    active_players, total_sessions}`。
  - `GET /api/players/stats/online-history?range=1h&exclude_bots=`：`data =
    {range, sample: "1m", points: [{t(epoch 秒), value}]}`（扫描线精确
    并发数，按分钟分桶，服务端降采样 ≤1500 点）。
  - `GET /api/players/stats/daily?range=7d&exclude_bots=`：`data =
    {range, points: [{date: "YYYY-MM-DD", players, sessions, playtime}]}`。
  - `GET /api/players/stats/players?exclude_bots=&limit=`：`data =
    {players: [{name, uuid?, online, sessions, total_playtime, avg_session,
    first_seen?, last_seen?}], total}`（累计时长来自聚合含历史，会话次数/
    平均每次来自会话日志）。
- `PUT /api/players/whitelist`：body `{enabled}` 开关；`POST
  /api/players/whitelist/reload`：重载。
- `PUT/DELETE /api/players/whitelist/{name}`：增删成员（自动重载）。
- `PUT/DELETE /api/players/{name}/op`：设/撤 OP。
- `POST /api/players/{target}/ban`：body `{type: "player"|"ip", reason?}`；
  `type` 非法 → **400** `invalid_type`。
- `POST /api/players/{target}/unban`：body `{type}`（改文件，重启生效）。
- `POST /api/players/{name}/kick`：body `{reason?}`。

动作失败错误码：`server_not_running`(400)、`command_failed`(400)、
`ban_not_found`(404)、`file_write_failed`(500)。
写审计：`whitelist.*`、`player.op|deop|ban|ban_ip|unban|unban_ip|kick`。
旧动作路径（POST 集合 + body 带 name/target）全部下线。

---

## 模组域（权限：管理员，工作目录 `mods/` 顶层 `.jar`/`.jar.disabled`）

文件操作响应统一 `{status, message?, data}`，`data` 含操作状态字段
`{server_running, needs_restart, effective_after: "restart"|"next_start",
warnings}`。

- `GET /api/mods`：`data = {mods, server_running, mods_path}`。条目
  `modified_at` 为 **epoch 秒**，含元数据/依赖/冲突/大小/警告等。
- `GET /api/mods/icon?filename=`：受限大小图标二进制（不走外壳）。
- `POST /api/mods/upload`（multipart：`file`/`enabled`/`acknowledge_warnings`）：
  超限 **413**、重名 **409**、损坏 **400**；兼容性警告首请求返回 **409** 且
  错误体 `data.warnings` 供二次确认，确认后带 `acknowledge_warnings=true`
  重提。成功 `data` 含 `mod` 与操作状态。
- `PUT /api/mods/{filename}/enabled`：body `{enabled, acknowledge_warnings?}`
  （旧 `POST /mods/toggle` + body filename 已下线）。
- `POST /api/mods/trash`（body `{filename}`）→ `GET /api/mods/trash`
  （分页外壳，`data.items`，`deleted_at` 为 epoch 秒）→ `POST
  /api/mods/trash/{id}/restore`、`DELETE /api/mods/trash/{id}?confirm=true`
  （仅超管；非超管 403 `super_admin_required`；缺 confirm 400
  `confirm_required`）。
- `GET /api/mods/configs?mod_id=&associated_only=`：`data.files`。
- `GET /api/mods/config?path=`：`data = {path, content?, config_data?}`；
  `PUT /api/mods/config`：body `{path, content?, config_data?}`。
  JSON/YAML/Properties 可结构化保存；JSON5/TOML/CFG/CONF 仅原文。路径限定
  `config/`、`defaultconfigs/`、`<level>/serverconfig/` 等安全根。
- `GET /api/mods/settings`：`data = {upload_max_mib, upload_max_bytes}`；
  `PUT /api/mods/settings`（仅超管）：body `{upload_max_mib}`（1–4096，越界
  **400** `invalid_value`）。

---

## 聊天 / pip / 任务型接口

### 公开聊天

聊天会话体系与 Web 管理端登录相互独立：验证码在游戏内由
`!!webui verify <code>` 绑定 → `PUT /chat/accounts/{name}/password` 签发
`session_id`（或用已有密码 `POST /chat/sessions` 登录）。时间字段
`timestamp` 为 epoch 秒。

公开聊天页是匿名入口，会话除下列 `/chat/*` 接口外，还可读取两个只读接口用于
页面展示：`GET /api/server/status?session_id=`（在线人数/版本）与
`GET /api/plugins/{plugin_id}?session_id=`（页脚版本号）。其余需要登录的接口
不接受聊天会话，管理类接口仍只认 WebUI 登录态。

- `POST /api/chat/verifications`：生成验证码 → `data {code, expire_minutes}`。
  未启用公开聊天 → **403** `public_chat_disabled`。
- `GET /api/chat/verifications/{code}`：查绑定 → `data {verified: bool,
  player_id?, message?}`；码不存在/过期 → **404** `verification_not_found` /
  **400** `verification_expired`。
- `PUT /api/chat/accounts/{name}/password`：body `{code, password}`；路径
  `name` 必须与码绑定玩家一致（否则 **400** `verification_mismatch`）；
  成功后**直接签发会话**，`data` 含 `session_id/player_id/uuid`。验证码猜测
  类失败计入 IP 级限流（**429** `too_many_attempts`）。
- `POST /api/chat/sessions`：body `{player_id, password}` → `data` 含
  `session_id`。账号不存在 **404** `user_not_found`；密码错 **401**
  `invalid_password`；IP 超限 **429** `ip_limit_exceeded`；失败次数超限
  **429** `too_many_attempts`（`data.retry_after`）。
- `GET /api/chat/session/{session_id}`：`data {valid, player_id?, uuid?}`；
  不存在 **404** `session_not_found`、过期 **401** `session_expired`。
- `DELETE /api/chat/session/{session_id}`：登出（幂等）。
- `GET /api/chat/messages`：权限为 WebUI 登录态，或启用公开聊天时携带
  `session_id` 的聊天会话（否则 **403** `public_chat_disabled` /
  **401**）。分页外壳（query `limit`(1–200)/`offset`/`after_id`/`before_id`）
  → `data.items`（新→旧）。
- `GET /api/chat/messages/incremental?after_id=&player_id=`：同上权限，
  轮询增量 → `data {messages, last_message_id, online: {web, game, bot}}`；
  聊天会话访客的心跳固定记为会话绑定的玩家，不能替他人上报在线。
- `DELETE /api/chat/messages`（管理员）：清空。
- `POST /api/chat/messages`：body `{message, player_id, session_id?}` 广播到
  游戏。需 `public_chat_to_game_enabled`（**403** `chat_to_game_disabled`）；
  Web 管理端已登录且 username==player_id 走管理员通道；否则校验会话
  （401 `invalid_session` / `session_player_mismatch` / `session_expired`，
  **429** `rate_limited`）。

旧路径 `/chat/get_messages|get_new_messages|check_session|check_verification|
send_message|set_password|logout|login|generate_code|clear_messages` 全部下线。

### pip 包管理（仪表盘）

- `GET /api/pip/packages`（管理员）：`data.packages: [{name, version}]`。
- `POST /api/pip/tasks`（管理员）：body `{action: "install"|"uninstall",
  package}`。非法 action → **400** `invalid_action`；空包名 → **400**
  `invalid_package`。返回 `data.task_id`，写审计 `pip.install|uninstall`。
- `GET /api/pip/tasks/{task_id}`（管理员）：`data.task_info`，结构同 PIM 任务
  （`status: running|completed|failed`；`all_messages` 逐行收集 pip
  stdout/stderr，`error_messages` 为错误子集）。不存在 → **404**
  `task_not_found`（前端停止轮询）；终态 30 分钟无访问回收。
- 任务终态只取 `completed|failed`，不再有“成功但永不结束”的轮询
  死循环；轮询条件显式三态判定。

---

## 审计 / WebUI 自身更新

### GET /api/audit_logs（管理员）

- query `offset`/`limit`（默认 0/50，上限 500）。分页外壳：`data.items`，
  条目 `{id, ts(epoch 秒整型), operation_type, summary, detail?, account?}`。
- 恒主服本地（不代理）。旧顶层 `records` 平铺已迁入 `data.items`。

### POST /api/self_update（管理员）

- 向 MCDR 发送 `!!MCDR plugin install -U -y guguwebui` 指令，
  返回 `{"status":"success","message":...}`（旧 `success` 双键已移除）。

### GET /api/self_update_info（管理员）

- `data`：更新信息对象（未采集时 `{"available": false}`；否则含
  `available/current/latest` 等）。

### POST /api/deepseek（管理员，AI 查询）

- body `{query, system_prompt?, model?, api_key?, api_url?}`；上游响应原样放
  入统一外壳 `data`（OpenAI 兼容结构如 `choices[0].message.content`）。
  业务错误 400/500 走统一错误体；未登录 401。

---

## 多服面板与配对

响应外壳、`data.phase`（pending/accepted/denied）状态机、入参 Pydantic 模型
（校验失败 422）、`expires_at` 等时间 epoch 秒。配对类请求不经过代理，须在
目标机直连。

- `GET /api/servers`（登录）：`data.servers: [{id, name, enabled, local}]`
  （`local` 取代旧驼峰 `isLocal`）。
- `GET/PUT /api/panel_merge_config`（管理员）：读 `data {panel_role,
  panel_slaves, panel_master}`；写 body `{panel_role: "master"|"slave",
  panel_slaves?, panel_master?}`，失败 **500** `config_write_failed`。
  旧 `POST /panel_merge_config` 已下线。
- 子服侧：`POST /api/pairing/enable` → `data.expires_at`（epoch 秒）；
  `POST /api/pairing/disable`；非子服 → **400** `role_mismatch`。
- 子服侧 `POST /api/pairing/request`（无需登录，body `{master_name?}`）：
  窗口外 → **403** `pairing_window_closed`；成功 `data {phase: "pending",
  request_id}`（首个请求后窗口自动关闭）。
- 子服侧 `GET /api/pairing/pending`（管理员）→ `data.pending`；
  `POST /api/pairing/accept|deny`（body `{request_id}`；accept 生成 token 写入
  `panel_master.allowed_tokens`，请求不存在 **404** `request_not_found`）。
- `GET /api/pairing/status?request_id=`（公开）：`data.phase`（accepted 时含
  `token`）。
- 主服侧 `POST /api/pairing/connect_request`（管理员，body
  `{slave_name, base_url}`）：请求子服 `/pairing/request`，不可达/拒绝 →
  **400** `pairing_request_failed`；成功 `data {phase: "pending",
  connect_id}`。
- 主服侧 `GET /api/pairing/connect_status?connect_id=`（管理员）：轮询子服
  `/pairing/status`；accepted 时把子服写入 `panel_slaves` 并返回
  `data {phase, server:{id,name,base_url}}`；`connect_id` 不存在 → **404**
  `connect_not_found`。跨服读取统一走 `data.phase` 契约。

---

## 保持现状 / 不做统一外壳的例外

- `/api/plugin/{plugin_id}/...`：第三方插件扩展点（见上）。
- `POST /api/login`：表单登录 + Cookie 会话；QQ 扫码端点（`code/qrUrl/state`
  顶层字段）随登录体系保留。
- 二进制响应：`/api/plugins/web-pages/{id}/icon`、`/api/mods/icon`、静态资源、
  自定义 css/js 不经外壳。
- SSE/长连接（未来日志实时推送）：另行设计，不属于本轮 REST 范围。

---

## 旧路径迁移速查（重构对照）

| 旧路径 | 新路径 |
|---|---|
| `GET /api/get_server_status` | `GET /api/server/status` |
| `POST /api/control_server` | `POST /api/server/controls` |
| `POST /api/send_command` | `POST /api/server/commands` |
| `GET /api/command_suggestions` | `GET /api/server/command-suggestions` |
| `GET /api/get_rcon_status` | `GET /api/server/rcon-status` |
| `POST /api/setup_rcon` | `POST /api/server/rcon-setup` |
| `GET /api/server_logs`、`/api/new_logs` | `GET /api/server/logs`（cursor 分页） |
| `GET /api/list_config_files?plugin_id=` | `GET /api/plugins/{id}/config-files` |
| `GET /api/load_config`、`/api/load_config_file*` | `GET /api/config-files?path=` |
| `POST /api/save_config`、`/api/save_config_file*` | `PUT /api/config-files?path=` |
| `GET /api/get_web_config` | `GET /api/web-config` |
| `POST /api/save_web_config` | `PUT /api/web-config` |
| `GET/POST /api/load_file`、`/api/save_file` | `GET/PUT /api/custom-assets/{kind}` |
| `GET /api/plugins`（?plugin_id= 过滤） | `GET /api/plugins/{plugin_id}` |
| `POST /api/toggle_plugin` | `PUT /api/plugins/{id}/enabled` |
| `POST /api/reload_plugin` | `POST /api/plugins/{id}/reload` |
| `GET /api/plugins/web_pages` | `GET /api/plugins/web-pages` |
| `GET /api/online-plugins`（裸数组） | `GET /api/plugins/online`（data.items） |
| `GET /api/pim/plugin_repository` | `GET /api/plugins/{id}/repository` |
| `GET /api/pim/plugin_versions` | `GET /api/plugins/{id}/versions` |
| `POST /api/pim/install_plugin` | `POST /api/plugins/{id}/install` |
| `POST /api/pim/update_plugin` | `POST /api/plugins/{id}/update` |
| `POST /api/pim/uninstall_plugin` | `POST /api/plugins/{id}/uninstall` |
| `GET /api/pim/task_status?task_id=` | `GET /api/pim/tasks/{task_id}` |
| `GET /api/check_pim_status` | `GET /api/pim/status` |
| `GET /api/install_pim_plugin` | `POST /api/pim/bootstrap` |
| `GET /api/pip/list` | `GET /api/pip/packages` |
| `POST /api/pip/install`、`/pip/uninstall` | `POST /api/pip/tasks` |
| `GET /api/pip/task_status?task_id=` | `GET /api/pip/tasks/{task_id}` |
| `GET /api/checkLogin` | `GET /api/auth/me` |
| `GET /api/langs` | `GET /api/i18n/languages` |
| `POST /chat/generate_code` | `POST /api/chat/verifications` |
| `POST /chat/check_verification` | `GET /api/chat/verifications/{code}` |
| `POST /chat/set_password` | `PUT /api/chat/accounts/{name}/password` |
| `POST /chat/login` | `POST /api/chat/sessions` |
| `POST /chat/check_session` | `GET /api/chat/session/{id}` |
| `POST /chat/logout` | `DELETE /api/chat/session/{id}` |
| `POST /chat/get_messages` | `GET /api/chat/messages` |
| `POST /chat/get_new_messages` | `GET /api/chat/messages/incremental` |
| `POST /chat/send_message` | `POST /api/chat/messages` |
| `POST /chat/clear_messages` | `DELETE /api/chat/messages`（管理员） |
| `POST /players/ban|unban|kick`、`POST /players/op|deop`、`POST /players/whitelist/*`（集合） | `POST /players/{target}/ban|unban|kick`、`PUT/DELETE /players/{name}/op`、`PUT/DELETE /players/whitelist/{name}`、`PUT /players/whitelist`、`POST /players/whitelist/reload` |
| `POST /mods/toggle` | `PUT /mods/{filename}/enabled` |
| `POST /panel_merge_config` | `PUT /api/panel_merge_config` |
| 旧 `/api/audit_logs` 顶层 records | `/api/audit_logs`（data.items 分页） |

> 本表为 REST 重构收尾基线。所有旧路径均已
> 下线、**不保留别名**；`panel_merge/proxy.py` 的“不代理”本地清单、
> `tests/test_proxy_local.py`、`tests/test_contract.py` 与 OpenAPI 快照
> `tests/snapshots/openapi_routers.json` 共同守护该基线。
