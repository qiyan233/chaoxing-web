# API 开发接口文档

本文描述 `chaoxing-web` 当前的 HTTP API、SSE 协议和后端扩展约定，核对日期为 2026-10-09。接口以 `webapp/routers/` 和 `webapp/schemas/` 为准；`api/` 是内部 Python 模块，不是独立 HTTP 服务。

> 学习任务固定串行，历史计数和重启行为仍有实现限制，接入前请阅读[当前行为与限制](#当前行为与限制)。示例使用虚构数据，涉及学习通账号或课程的请求需替换为自己的测试数据。

- [请求约定与鉴权](#请求约定与鉴权)
- [接口索引](#接口索引)
- [认证与服务状态](#认证与服务状态)
- [平台用户](#平台用户)
- [学习通账号与课程](#学习通账号与课程)
- [任务与日志](#任务与日志)
- [SSE 实时事件](#sse-实时事件)
- [全局设置](#全局设置)
- [本地代理池](#本地代理池)
- [更新与重启](#更新与重启)
- [后端扩展约定](#后端扩展约定)

## 请求约定与鉴权

### 地址与格式

- 默认 Base URL：`http://localhost:3000`；JSON 接口以 `/api` 开头，健康检查为 `/healthz`。
- 请求体使用 `Content-Type: application/json`。无请求体操作无需发送空对象。
- `{account_id}`、`{task_id}`、`{user_id}`、`{proxy_id}` 均为整数路径参数。
- 创建用户、账号、任务和代理通常返回 `201`；删除用户、账号和单个代理返回 `204`，没有响应体；其他接口默认 `200`。
- 没有统一响应包装：返回模型对象、数组、`{status,msg}` 或独立结构。`status` 也可能为字符串，不能统一作为布尔值处理。
- ORM 时间输出为 ISO 8601 字符串，当前多数字段无时区偏移。任务时间使用 `datetime.utcnow()`，数据库默认时间由 SQLite 生成；服务器面板时间使用宿主机本地时间。SSE 的 `ts` 为 Unix 秒，可包含小数。
- 仪表盘任务/日志条目和全部日志查询显式返回 UTC 时间（带 `Z`）；趋势及今日/本月统计使用香港时间（UTC+8）。普通 ORM 响应不自动补充这个后缀。
- 自动接口参考：`/docs`、`/redoc`、`/openapi.json`。SSE 和无响应模型接口需结合本文；`/healthz` 不包含在 OpenAPI 中。

### Cookie 会话

登录成功后服务端通过 `Set-Cookie` 设置 `chaoxing_session`。后续请求携带 Cookie，登出清空 Session。当前没有 API key、Bearer token 或 OAuth。

会话 Cookie 默认 `HttpOnly`、`SameSite=Lax`、有效期 7 天，当前 `https_only=False`。Session 内容通过签名保护，不用于保存学习通密码。服务端未配置跨域 CORS 中间件，浏览器客户端应从同源页面调用。

| 权限 | 含义 | 典型接口 |
| --- | --- | --- |
| 公开 | 无需登录 | 登录、初始化、登出、健康检查 |
| 登录 | 管理员或普通用户 | 账号、课程、任务；普通用户按账号归属访问 |
| 管理员 | Session 为管理员身份 | 用户、设置、代理池、更新、服务器统计 |

普通用户以 `chaoxing_accounts.user_id` 判断账号和任务归属，SSE 在回放或订阅前使用与任务详情相同的归属检查。用户禁用状态仍在登录时检查，现有 Session 的鉴权依赖不会再次查询用户是否被禁用。

管理员依赖、账号/任务授权和页面判断统一使用 `deps.is_admin_session()`。存在显式 role 时只接受 role=admin，普通用户即使用户名为 admin 也不会获得管理员权限；只有缺少 role 的旧管理员会话才兼容历史用户名判断。

### 错误处理

HTTP 异常通常返回：

```json
{"detail": "未登录"}
```

校验失败返回 `422`，`detail` 为数组，例如：

```json
{
  "detail": [
    {"type": "missing", "loc": ["body", "account_id"], "msg": "Field required", "input": {}}
  ]
}
```

| 状态码 | 典型原因 | 处理方式 |
| --- | --- | --- |
| `400` | 重名、重复手机号、账号已有任务、无效批量操作 | 展示 detail 并修正请求 |
| `401` | 未登录、密码错误、提供的旧密码错误 | 重新登录或检查密码 |
| `403` | 非管理员、越权、禁用用户登录 | 检查权限 |
| `404` | 资源不存在 | 刷新列表，停止使用旧 ID |
| `422` | 类型、范围、必填项或枚举错误 | 按 loc 修正字段 |
| `500` | 拉取课程异常或未处理错误 | 查看日志；响应不保证一定为 JSON |
| `502` | SCDN 导入的外部请求失败 | 检查外部服务与连通性 |

HTTP 成功不等于业务成功：重新登录、代理测试、取消和更新等可能返回 `status=false` 或 `ok=false`。创建账号验证失败仍返回 `201` 和 `status="error"`。

网页公共助手 `apiFetch(url, options)` 返回原始 Response，但在网络失败、非 JSON 响应、HTTP 错误或 `status === false` 时抛出带消息的错误；`204` 响应直接返回。代理测试的 `ok=false`、账号的 `status="error"`、批量响应的 failed 明细仍由各操作单独判断。`actionPage()` 负责将错误显示为页面提示，失败不会继续执行成功跳转。自定义客户端应采用同样的业务结果判断，不能把所有 status 字段转换为布尔值。

### 最小调用示例

从项目目录执行，只登录并读取账号列表，不创建任务：

```shell
uv run python -c "import requests; s=requests.Session(); r=s.post('http://localhost:3000/api/admin/login', json={'password':'admin123'}); r.raise_for_status(); r=s.get('http://localhost:3000/api/accounts'); r.raise_for_status(); print(r.json())"
```

密码仅适用于首次初始化的测试实例，修改后需替换。`requests.Session` 自动保持 Cookie。

## 接口索引

下表包含全部 JSON 接口与 SSE，不包含 HTML、静态资源和自动文档路由。「登录」接口仍受资源归属约束。

| 方法 | 路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| GET | `/healthz` | 公开 | 健康检查 |
| POST | `/api/setup` | 公开，仅未初始化时 | 初始管理员密码 |
| POST | `/api/login` | 公开 | 管理员登录兼容入口 |
| POST | `/api/admin/login` | 公开 | 管理员登录 |
| POST | `/api/user/login` | 公开 | 普通用户登录 |
| POST | `/api/logout` | 公开 | 清空当前会话 |
| POST | `/api/admin/password` | 管理员 | 修改管理员密码 |
| GET | `/api/admin/server-status` | 管理员 | 服务器与业务统计 |
| GET | `/api/users` | 管理员 | 用户列表 |
| POST | `/api/users` | 管理员 | 创建用户 |
| GET | `/api/users/overview` | 管理员 | 用户及绑定账号概览 |
| GET | `/api/users/account-options` | 管理员 | 全部可绑定账号 |
| PUT | `/api/users/{user_id}` | 管理员 | 修改密码或状态 |
| DELETE | `/api/users/{user_id}` | 管理员 | 删除用户 |
| PUT | `/api/users/{user_id}/accounts/{account_id}` | 管理员 | 绑定或转移归属 |
| DELETE | `/api/users/{user_id}/accounts/{account_id}` | 管理员 | 解绑账号 |
| GET | `/api/accounts` | 登录 | 学习通账号列表 |
| POST | `/api/accounts` | 登录 | 添加账号 |
| PUT | `/api/accounts/{account_id}` | 登录 | 修改密码/昵称 |
| DELETE | `/api/accounts/{account_id}` | 登录 | 删除账号 |
| POST | `/api/accounts/{account_id}/login` | 登录 | 验证学习通登录 |
| GET | `/api/accounts/{account_id}/courses` | 登录 | 获取课程 |
| GET | `/api/tasks/logs` | 登录 | 按权限查询全部日志，支持筛选和分页 |
| POST | `/api/tasks` | 登录 | 创建任务 |
| GET | `/api/tasks` | 登录 | 任务列表 |
| GET | `/api/tasks/{task_id}` | 登录 | 任务详情 |
| GET | `/api/tasks/{task_id}/logs` | 登录 | 持久化日志 |
| POST | `/api/tasks/{task_id}/cancel` | 登录 | 取消任务 |
| GET | `/api/tasks/{task_id}/stream` | 登录，检查归属 | SSE 订阅 |
| GET | `/api/settings/tiku` | 管理员 | 读取题库配置 |
| POST | `/api/settings/tiku` | 管理员 | 保存题库配置 |
| GET | `/api/settings/notification` | 管理员 | 读取通知配置 |
| POST | `/api/settings/notification` | 管理员 | 保存通知配置 |
| GET | `/api/settings/proxy` | 管理员 | 读取代理来源 |
| POST | `/api/settings/proxy` | 管理员 | 保存代理来源 |
| GET | `/api/settings/runtime` | 管理员 | 读取运行配置 |
| POST | `/api/settings/runtime` | 管理员 | 保存运行配置 |
| GET | `/api/proxies` | 管理员 | 本地代理列表 |
| POST | `/api/proxies/test` | 管理员 | 测试地址，不入库 |
| POST | `/api/proxies` | 管理员 | 测试后新增/更新代理 |
| POST | `/api/proxies/import/scdn` | 管理员 | SCDN 拉取、测试、入库 |
| POST | `/api/proxies/bulk/test` | 管理员 | 批量测试 |
| POST | `/api/proxies/bulk/delete` | 管理员 | 批量删除 |
| POST | `/api/proxies/{proxy_id}/test` | 管理员 | 复测代理 |
| POST | `/api/proxies/{proxy_id}/enable` | 管理员 | 标记可用 |
| POST | `/api/proxies/{proxy_id}/disable` | 管理员 | 禁用代理 |
| DELETE | `/api/proxies/{proxy_id}` | 管理员 | 删除单个代理 |
| GET | `/api/update/status` | 管理员 | 检查代码更新 |
| POST | `/api/update/apply` | 管理员 | 拉取代码并同步依赖 |
| POST | `/api/update/restart` | 管理员 | 延迟重启 |

## 认证与服务状态

### 初始化、登录与登出

| 接口 | JSON 请求体 | 成功响应 | 特有错误/行为 |
| --- | --- | --- | --- |
| `POST /api/setup` | new_password 必填，4～128；old_password 可选但不使用 | `{"status":true,"msg":"初始化成功"}`，同时登录 | 已初始化 `400`；正常启动自动初始化，通常无需调用 |
| `POST /api/login` | password: string，必填 | `{"status":true,"msg":"登录成功"}` | 未初始化 `400`，密码错误 `401` |
| `POST /api/admin/login` | 同 /api/login | 同 /api/login | 兼容封装 |
| `POST /api/user/login` | username 长度 3～64；password 长度 1～128，均必填 | `{"status":true,"msg":"登录成功","role":"user"}` | 不存在/密码错误 `401`，禁用 `403` |
| `POST /api/logout` | 无 | `{"status":true,"msg":"已登出"}` | 无需已有登录状态 |

管理员只提交密码，没有用户名字段；管理员密码存在 `app_settings`，管理员不是 `platform_users` 表中的用户。普通登录会移除用户名首尾空白。

### 修改管理员密码

`POST /api/admin/password`：

```json
{"new_password": "example-new-password", "old_password": "admin123"}
```

new_password 必填，长度 4～128；old_password 可选，当前仅在提供非空旧密码时校验，错误返回 `401`。成功返回 `{"status":true,"msg":"密码已更新"}`，不会主动登出其他已有 Session。

### 健康与服务器统计

`GET /healthz` 无参数，返回 `{"status":"ok"}`；不检测数据库和调度器。

`GET /api/admin/server-status` 无参数，响应字段：

| 字段 | 类型 | 内容/单位 |
| --- | --- | --- |
| status | string | ok |
| server_time、started_at | string | 宿主机本地时间，YYYY-MM-DD HH:MM:SS |
| uptime_seconds | integer | 当前进程运行秒数 |
| runtime | object | python, system, release, machine, pid, cpu_count, load_avg；load_avg 可为 null |
| resources.cpu | object | percent, sample_interval；首次采样可能为 null |
| resources.disk | object | total, used, free 字节数，percent 百分比，path 数据目录 |
| resources.memory | object | total, used, available 字节数，percent；采集失败时各值可为 null |
| resources.network | object | rx_total, tx_total 字节；rx_speed, tx_speed 字节/秒；sample_interval 秒；首次采样或不支持时可为 null |
| business | object | 整数计数 users, active_users, accounts, running_accounts, tasks, pending_tasks, running_tasks, failed_tasks, proxies, active_proxies |
| dashboard | object | 仪表盘汇总：date、accounts、active_accounts、running、pending、completed、today_completed、month_completed、total_tasks、failed、proxy_total、proxy_active，以及 trend、recent_tasks、active_tasks、queue_tasks、logs |

dashboard 的全量计数不受最近任务列表限制。正常账号统计 idle/running；failed 合计 failed/partial。日期使用香港时间（UTC+8），今日/本月完成数仅统计有 finished_at 的 completed 任务，不推测历史完成时间。trend 连续返回最近 30 天的 date、created（新建数）、completed（完成数）。

recent_tasks 最多 20 条按任务 ID 倒序；active_tasks 最多 4 条，运行中优先、排队按 ID 升序；queue_tasks 为最早的 5 条排队任务。任务条目只包含 ID、account_id、account_name、状态、当前课程/章节、章节计数及 updated_at（结束、开始、创建时间依次回退，UTC ISO 字符串）。logs 为最近 8 条，包含 id、task_id、level、message、account_name、created_at（UTC ISO 字符串）。此接口仍仅允许管理员访问，不返回密码或 Cookie 字段。

| 仪表盘字段组 | 类型 / 说明 |
| --- | --- |
| date | string，香港日期 `YYYY-MM-DD` |
| accounts、active_accounts | integer，总账号数、idle/running 账号数 |
| running、pending、completed、failed、total_tasks | integer；failed 合计 failed/partial，completed 保留历史状态统计 |
| today_completed、month_completed | integer；仅统计有 finished_at 的 completed 任务 |
| proxy_total、proxy_active | integer；本地代理记录总数、active 数 |
| trend | object[]，固定 30 项，每项 date: string、created: integer、completed: integer |
| recent_tasks、active_tasks、queue_tasks | object[]，相同的任务摘要结构，数量限制见上文 |
| logs | object[]，最新 8 条持久化日志；完整查询使用 `/api/tasks/logs` |

## 平台用户

全部要求管理员。`PlatformUserResponse` 包含 `id: integer, username: string, role: string, status: string, created_at: datetime, updated_at: datetime`，不返回密码哈希。

### 列表、创建、更新与删除

| 接口 | 参数或请求体 | 返回 | 特有错误 |
| --- | --- | --- | --- |
| `GET /api/users` | 无 | 按 ID 升序的用户数组 | 无 |
| `POST /api/users` | username 必填，3～64；password 必填，4～128；status 默认 active，可选 disabled | 201 用户对象，role 固定 user | 去空白后为空或重名 `400` |
| `PUT /api/users/{user_id}` | password 可选，4～128；status 可选，active/disabled | 更新后的用户对象 | 不存在 `404` |
| `DELETE /api/users/{user_id}` | 无请求体 | 204 | 不存在 `404` |

创建示例：

```json
{"username": "demo_user", "password": "example-user-password", "status": "active"}
```

更新不能修改用户名或角色。删除不主动取消任务或撤销 Session。模型声明账号外键 `ON DELETE SET NULL`，但当前 SQLite 连接未显式开启外键约束，不能依赖删除操作自动解绑，应先显式解绑。

### 绑定学习通账号

- `GET /api/users/overview`：用户数组，每个用户对象附加 `account_count: integer`、`accounts: array`。账号摘要为 id, phone, nickname, status, last_login_at, last_error。
- `GET /api/users/account-options`：全部学习通账号数组，每项为 id, user_id, phone, nickname, status；user_id 可为 null。
- `PUT /api/users/{user_id}/accounts/{account_id}`：无请求体，直接设定归属，可覆盖已有绑定。返回 `{"status":true,"msg":"绑定成功"}`；用户或账号不存在 `404`。
- `DELETE /api/users/{user_id}/accounts/{account_id}`：无请求体，将归属设为 null。返回 `{"status":true,"msg":"已解除绑定"}`；账号不存在 `404`，归属不符 `400`。

## 学习通账号与课程

管理员访问全部账号，普通用户只操作自己的账号。普通用户新增账号绑定自身，管理员新增账号归属为 null，之后可绑定用户。

### 响应模型

| AccountResponse 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 学习通账号 ID |
| phone | string | 手机号/登录账号 |
| nickname | string/null | 昵称；验证后可能更新为超星资料昵称 |
| status | string | 常见值 idle/running/error |
| last_login_at | datetime/null | 最近验证登录成功时间 |
| last_error | string/null | 最近错误 |
| created_at | datetime | 创建时间 |

不返回密码、密文、Cookie 或 user_id，查询归属使用 `/api/users/account-options`。

### 账号管理

| 接口 | 请求体 | 返回 | 行为 |
| --- | --- | --- | --- |
| `GET /api/accounts` | 无 | AccountResponse[]，ID 升序 | 普通用户只见自己的账号 |
| `POST /api/accounts` | phone 必填，5～32；password 必填，1～128；nickname 可选，最多 64；verify_login 默认 true | 201 AccountResponse | 手机号全局唯一，重复 `400`；验证失败仍保存 |
| `PUT /api/accounts/{account_id}` | password 可选，1～128；nickname 可选，最多 64 | AccountResponse | 修改密码清除 Cookie，不自动验证；null 昵称不修改，空字符串可清空 |
| `DELETE /api/accounts/{account_id}` | 无 | 204 | 不主动取消任务，删除前应先取消并等待结束 |

现有账号的更新、删除、登录、课程查询：不存在 `404`，越权 `403`。

添加示例，手机号为虚构值：

```json
{"phone": "13800000000", "password": "example-chaoxing-password", "nickname": "测试账号", "verify_login": false}
```

### 登录验证与课程查询

`POST /api/accounts/{account_id}/login` 无请求体。优先验证 Cookie，再回退密码登录，更新账号状态。成功通常返回：

```json
{"status": true, "msg": "Cookie 校验通过", "nickname": "示例昵称"}
```

nickname 可省略，msg 随路径变化。失败通常为 `{"status":false,"msg":"登录失败原因"}`，HTTP 仍为 200。

`GET /api/accounts/{account_id}/courses?refresh=false`：refresh 为可选布尔查询参数，默认 false；true 绕过课程缓存。响应示例：

```json
{
  "account_id": 1,
  "courses": [
    {"courseId": "100001", "clazzId": "200001", "cpi": "300001", "title": "示例课程", "teacher": "示例教师", "desc": ""}
  ],
  "count": 1
}
```

课程以上字段均为字符串，courseId 用于任务创建。缓存默认 300 秒，由 `CHAOXING_COURSE_CACHE` 控制。拉取异常 `500`，detail 为 `拉取课程失败: ...`。

## 任务与日志

### 全部日志查询

`GET /api/tasks/logs` 要求登录，管理员查看全部；普通用户仅查询归属自己账号的日志，所有筛选和计数均应用此权限范围。静态路由在 `/{task_id}` 之前注册。

| 参数 | 默认 / 限制 | 行为 |
| --- | --- | --- |
| q | 空，最多 200 字符 | 日志内容、账号昵称或手机号的子串搜索；% 和 _ 按普通字符处理 |
| level | 空 | 空或 info/success/warning/error/debug |
| limit | 50，1～200 | 每页条数 |
| offset | 0，非负 | 跳过条数 |

返回 `{ "total": 0, "items": [] }`，items 按日志 ID 倒序，字段为 id、task_id、level、message、account_name、created_at（UTC，带 Z）。total 是权限范围内匹配筛选条件的总条数。参数非法返回 422。

| 响应字段 | 类型 | 说明 |
| --- | --- | --- |
| total | integer | 匹配权限与筛选条件的日志总数 |
| items | object[] | 当前页，空结果返回空数组 |
| items[].id、items[].task_id | integer | 日志 ID、关联任务 ID |
| items[].level、items[].message | string | 级别、原始日志文本 |
| items[].account_name | string | 优先账号昵称，否则登录账号 |
| items[].created_at | string | UTC ISO 8601 时间，带 Z |

已登录的同源页面可直接调用：

```javascript
const query = new URLSearchParams({level: 'warning', limit: '50', offset: '0'});
const response = await fetch(`/api/tasks/logs?${query}`);
const data = await response.json();
if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '查询失败');
console.log(`匹配 ${data.total} 条日志，本页 ${data.items.length} 条`);
```

下一页增加 offset，直到 `offset + limit >= total`；修改筛选条件时把 offset 重置为 0。没有轮询、SSE 或 Last-Event-ID 语义，刷新需重新 GET。

对应页面：管理员 `/logs`、普通用户 `/user/logs`；新建任务向导入口为 `/tasks/new` 和 `/user/tasks/new`，选择账号后沿用课程页与创建任务接口。

### 创建任务

`POST /api/tasks` 成功为 `201 TaskResponse`。同账号已有 pending/running 时 `400`，账号不存在 `404`，无权使用账号 `403`。

```json
{
  "account_id": 1,
  "course_ids": ["100001"],
  "speed": 1.0,
  "jobs": 1,
  "notopen_action": "continue",
  "mode": "normal"
}
```

| 字段 | 类型 | 必填/默认 | 当前行为 |
| --- | --- | --- | --- |
| account_id | integer | 必填 | 学习通账号 ID |
| course_ids | string[]/null | null | 缺省、null、空数组都表示全部课程；非空数组按 ID 过滤 |
| speed | number | 1.0 | 1.0～2.0 |
| jobs | integer | 1 | 兼容字段，只接受 1，其他值返回 422；任务点串行执行 |
| notopen_action | string | retry | retry/ask/continue；当前未落实回退重试/询问 |
| mode | string | normal | normal/quick，quick 失败可回退正常模式 |

先提交数据库再提交调度器，没有幂等键。请求超时后先查列表再决定是否重试。不同账号的任务按提交顺序排队；返回时任务可能已从 pending 进入 running。普通与快速模式的任务点均串行执行。

页面始终发送明确的所选 course_ids，并拒绝空/失效选择；API 为兼容仍把空数组解释为全部课程。客户端应在提交前校验选择，避免误启动全部课程。页面只提供实际生效的 continue 锁定章节策略；retry/ask 仍是兼容请求值，不承诺执行重试或询问。

创建时校验账号和参数，执行时再核对课程。无课程、所选课程全部或部分不存在/不可访问、零章节会将任务标为 failed 并给出原因；创建请求的 201 不表示课程校验或执行已成功。

### 任务响应模型

| TaskResponse 字段 | 类型 | 说明 |
| --- | --- | --- |
| id、account_id | integer | 任务 ID、账号 ID |
| status | string | pending/running/completed/partial/failed/cancelled |
| course_ids | string/null | **JSON 编码字符串**，不是数组；全部课程时为 null |
| speed | number | 倍速 |
| jobs | integer | 新任务固定为 1；旧任务保留历史值，不影响固定串行策略 |
| notopen_action、mode | string | 保存的策略与模式 |
| total_chapters、done_chapters | integer | 总章节数、已处理章节数 |
| succeeded_chapters | integer | 章节处理器返回 SUCCESS 的数量 |
| failed_chapters | integer | 返回 ERROR、未知/PENDING 或发生章节异常的数量 |
| skipped_chapters | integer | 返回 NOT_OPEN 的数量 |
| current_course、current_chapter | string/null | 当前或最近处理的课程/章节 |
| started_at、finished_at | datetime/null | 开始和结束时间 |
| error_message | string/null | 任务级错误或取消原因 |
| created_at | datetime | 创建时间 |

客户端用 `JSON.parse(task.course_ids)` 或等价逻辑读取非空课程列表。done_chapters 包含错误和未开放章节；新任务正常统计满足 `done_chapters = succeeded_chapters + failed_chapters + skipped_chapters`，取消保留已处理结果。

只有非空章节全部返回 SUCCESS 才为 completed；任意章节失败、跳过或未知结果为 partial，表示流程结束但未全部成功（即使所有章节都失败/跳过也使用 partial）。登录、课程核对等任务级异常使用 failed。成功统计不构成超星服务端最终通过证明。

升级为旧任务追加的三个计数字段初始为 0，不回填历史成功数，不改变旧状态。`done_chapters` 大于三类合计时，差额是历史未分类结果；客户端应单独展示。

### 查询与取消

| 接口 | 查询参数/请求体 | 返回 | 排序/行为 |
| --- | --- | --- | --- |
| `GET /api/tasks` | account_id: integer 可选；limit: integer=50 | TaskResponse[] | ID 降序，普通用户按归属过滤 |
| `GET /api/tasks/{task_id}` | 无 | TaskResponse | 不存在 `404`，越权 `403` |
| `GET /api/tasks/{task_id}/logs` | limit: integer=200；after_id: integer=0 | TaskLogResponse[] | ID 升序，仅 id > after_id |
| `POST /api/tasks/{task_id}/cancel` | 无请求体 | status: boolean、msg: string | 发送取消信号，数据库残留任务可直接标记 cancelled |

limit 和 after_id 无额外范围校验，客户端使用合理正整数。列表没有页码/总数包装。增量日志以最后一条 ID 更新 after_id。

`TaskLogResponse` 为 `id: integer, task_id: integer, level: string, message: string, created_at: datetime`。只有执行器主动记录的日志持久化，不是所有底层 Loguru 输出都会入表。日志和取消也检查任务存在与归属。

取消成功示例：

```json
{"status": true, "msg": "已发送取消信号"}
```

终态任务返回 status=false，HTTP 仍为 200。信号成功不表示已经结束，需查询状态或等待终态事件。

## SSE 实时事件

### 建立连接

`GET /api/tasks/{task_id}/stream` 返回 `text/event-stream`，无请求体，沿用登录 Cookie；不存在返回 404，无权访问返回 403。归属校验发生在终态回放和订阅队列之前。

同源浏览器示例，taskId 取创建响应的 id：

```javascript
function watchTask(taskId) {
  const stream = new EventSource(`/api/tasks/${taskId}/stream`);
  stream.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    console.log(event);
    if (["task_done", "task_partial", "task_failed", "task_cancelled", "task_replay_end"].includes(event.type)) {
      stream.close();
    }
  };
  stream.onerror = () => console.warn("连接中断，请查询任务状态并按需重连");
  return stream;
}
```

服务端使用普通 message 事件，类型放在 JSON 的 type 中，没有 `event:` 命名事件或 `id:` 游标。每条消息以空行结束：

```text
data: {"type":"chapter_finished","course_title":"示例课程","chapter_title":"第一章","result":"ok","done":1,"total":3}

```

### 事件字段

| type | 附加字段 | 含义 |
| --- | --- | --- |
| snapshot | status, done, total, current_course, current_chapter | 活跃任务连接时的初始状态 |
| task_started | ts | 后台开始 |
| logged_in | ts | 学习通登录成功 |
| courses_loaded | count | 选中课程数 |
| total_calculated | total_chapters | 总章节数 |
| course_started | course_title | 课程开始 |
| chapter_started | course_title, chapter_title, done, total | 章节开始 |
| video_progress | job_id, job_name, play_time, duration, ts | 播放秒数与总秒数，正常视频/音频流程发出 |
| job_done | job_id, job_name, kind, ts | 正常视频/音频结束，当前 kind 为 video |
| chapter_finished | course_title, chapter_title, result, done, total | result 为 ok/not_open/error |
| course_finished | course_title | 课程遍历结束 |
| log | level, message | 执行器日志，无数据库日志 ID |
| task_done | ts, message | 流程正常结束 |
| task_partial | ts, message | 流程结束，但章节未全部成功 |
| task_failed | ts, message | 任务级异常 |
| task_cancelled | ts, message | 已取消 |
| task_replay_end | status, done, total, error | 连接到已结束任务，只返回此状态事件 |

snapshot、task_replay_end、chapter_started、chapter_finished 以及实时终态事件都包含 succeeded_chapters、failed_chapters、skipped_chapters。实时终态还包含 status、done、total 和对应的 done_chapters、total_chapters；客户端应在 task_partial 后关闭连接。

ts 不在所有事件中出现。done/total 为整数，current_course/current_chapter/error 可为 null，job_id/job_name 来自元数据也可能为空。

### 心跳、重连与历史

- 空闲 15 秒发 `: ping` 注释心跳，不触发浏览器 onmessage。
- 终态后服务端关闭连接，客户端主动 close，避免自动重连。
- 没有 Last-Event-ID 重放；重连后查询详情和 `/logs?after_id=...` 恢复信息。
- task_replay_end 不代表日志已重放，历史日志另行读取。
- 每订阅队列最多 500 条，满时丢弃旧事件，不保证收齐全部日志和进度样本。
- 极早失败、连接与结束之间的竞争可能缺终态事件；onerror 或长时间无进度时查询详情兜底。
- quick 成功走快速路径时不会提供完整播放进度事件。
- 反向代理需允许长连接、关闭缓冲；接口提供 `X-Accel-Buffering: no`，仍需结合代理配置。

## 全局设置

全部要求管理员，每项提供 GET 和 POST。GET 返回完整配置和默认值，POST **保存整个模型，省略项以默认值覆盖原值**。局部修改先 GET 再合并 POST。题库读取返回 token/key 原值，不要输出到公共日志。

### 题库配置

`GET /api/settings/tiku` / `POST /api/settings/tiku`。成功返回 `{"status":true,"msg":"题库配置已保存"}`，新任务生效。

| 字段 | 类型 | 默认值/约束 | 用途 |
| --- | --- | --- | --- |
| provider | string | 空字符串 | 空值禁用；TikuYanxi/TikuLike/TikuAdapter/AI/SiliconFlow，大小写一致 |
| submit | boolean | false | 自动提交开关；false 只保存 |
| cover_rate | number | 0.9，0～1 | 搜到答案占比阈值，不是正确率 |
| delay | number | 1.0，>=0 | 查询间隔参数，秒 |
| tokens | string | 空字符串 | 言溪/LIKE token，多个用逗号分隔 |
| url | string | 空字符串 | TikuAdapter 地址 |
| endpoint、key、model | string | 各为空字符串 | AI 地址、密钥、模型 |
| min_interval_seconds | integer | 3，>=0 | AI 最小请求间隔 |
| http_proxy | string | 空字符串 | AI 代理，与课程代理配置分开 |
| siliconflow_key | string | 空字符串 | SiliconFlow 密钥 |
| siliconflow_model | string | deepseek-ai/DeepSeek-R1 | SiliconFlow 模型 |
| siliconflow_endpoint | string | https://api.siliconflow.cn/v1/chat/completions | SiliconFlow 地址 |
| likeapi_search | boolean | false | LIKE 搜索选项 |
| likeapi_vision | boolean | true | LIKE 视觉选项 |
| likeapi_model | string | glm-4.5-air | LIKE 模型 |
| likeapi_retry | boolean | true | LIKE 重试开关 |
| likeapi_retry_times | integer | 3 | 重试次数，模型无额外范围限制 |
| true_list | string | 正确,对,√,是 | 判断题正确匹配项，逗号分隔 |
| false_list | string | 错误,错,×,否,不对,不正确 | 判断题错误匹配项，逗号分隔 |
| check_llm_connection | boolean | true | 保留的连接检查配置；Web 执行器不直接调用连接检查方法 |

HTTP 层 provider 是普通字符串，不校验类名，无效值可能保存成功后在任务初始化报错。内部保存会把布尔和数字转成字符串以适配 Tiku；客户端仍发送正常 JSON 类型。

### 通知配置

`GET /api/settings/notification` / `POST /api/settings/notification`。字段均为字符串：provider 默认空（说明列出 ServerChan/Qmsg/Bark/Telegram）、url 默认空、tg_chat_id 默认空。成功为 `{"status":true,"msg":"通知配置已保存"}`。

当前只读写配置，任务执行器没有调用通知服务，保存成功不表示会发送通知；页面已移除通知操作入口。

### 代理来源配置

`GET /api/settings/proxy` / `POST /api/settings/proxy`。成功为 `{"status":true,"msg":"代理池配置已保存，新任务生效"}`。

| 字段 | 类型 | 默认值/约束 | 说明 |
| --- | --- | --- | --- |
| enabled | boolean | false | 启用课程/登录代理 |
| source | string | manual；manual/scdn/local | 手动、动态或本地库 |
| proxies | string | 空字符串 | 一行一个，支持 host:port/带协议地址 |
| strategy | string | random；random/round_robin | 随机或按 seed 取模，并非全局轮转计数器 |
| failover | boolean | true | 兼容保存字段，尚未实现自动失败切换，页面无此开关 |
| scdn_url | string | https://proxy.scdn.io/text.php | 动态来源地址 |
| scdn_protocol | string | http；http/https | 动态协议 |
| scdn_country | string | 空字符串 | 地区过滤 |
| scdn_quantity | integer | 50，1～5000 | 动态请求数量 |
| scdn_cache_seconds | integer | 300，0～86400 | 候选缓存秒数 |

登录/课程的 seed 是账号 ID，任务 seed 是任务 ID。local 只选择 active；本地池或 SCDN 为空时回退手动列表，最终没有候选时直接访问服务，不强制报错。

### 运行配置

`GET /api/settings/runtime` / `POST /api/settings/runtime`。唯一字段 max_concurrent_accounts: integer，固定为 1。GET 始终返回实际串行策略，旧数据库配置和 CHAOXING_MAX_ACCOUNTS 均不再改变执行账号数；POST 其他值返回 422。

```json
{"max_concurrent_accounts": 1}
```

成功为 `{"status":true,"msg":"运行配置已保存，学习任务固定串行执行"}`。此接口为兼容保留，不能开启账号或章节任务点并发。

## 本地代理池

全部要求管理员。状态为 active/failed/disabled，批量测试按条执行，客户端需为总操作预留时间。

### 模型

| 请求模型/字段 | 类型 | 默认/约束 |
| --- | --- | --- |
| 公共测试选项 test_mode | string | chaoxing，可选 generic |
| 公共测试选项 test_url | string | 空字符串，最多 512；空时使用对应模式默认地址 |
| 公共测试选项 timeout_seconds | number | 8.0，1～30；单次请求超时秒数 |
| ProxyTestRequest | object | 公共选项 + proxy_url: string，默认空，最多 255 |
| ProxyAddRequest | object | 公共选项 + proxy_url: string，必填，最短 3，可为多行 |
| ScdnProxyImportRequest | object | 公共选项 + protocol 默认 http（http/https/socks4/socks5/all）；count 默认 10，1～20；country_code 默认空，最多 2 |
| ProxyBulkTestRequest | object | 公共选项 + ids: integer[]，默认空，但接口要求非空 |
| ProxyBulkActionRequest | object | ids: integer[] 默认空，status: string/null 默认 null（active/failed/disabled） |

默认超星测试检查入口，再用空凭据探测登录接口可达性，通过不代表真实账号一定能登录。自定义 test_url 时只测试指定目标。SCDN 导入的协议枚举与动态配置不同；SOCKS 还依赖 requests 的 SOCKS 扩展，当前依赖未显式安装该扩展。

| 响应模型 | 字段 |
| --- | --- |
| ProxyResponse | id: integer, proxy_url: string, status: string, latency_ms: integer/null, last_tested_at: datetime/null, fail_reason: string/null, created_at: datetime, updated_at: datetime |
| ProxyTestResult | ok: boolean, proxy_url: string, latency_ms: integer/null, status_code: integer/null, error: string/null, origin: string/null |
| ProxyAddResponse | status: boolean, msg: string, added: ProxyResponse[], failed: ProxyTestResult[] |
| ProxyBulkTestResponse | status: boolean, msg: string, tested: integer, passed: integer, failed: integer, results: ProxyTestResult[] |
| ProxyBulkDeleteResponse | status: boolean, msg: string, deleted: integer |

### 接口行为

| 接口 | 输入 | 响应/行为 |
| --- | --- | --- |
| `GET /api/proxies` | 无 | ProxyResponse[]，ID 降序 |
| `POST /api/proxies/test` | ProxyTestRequest | ProxyTestResult，不入库 |
| `POST /api/proxies` | ProxyAddRequest | 201 ProxyAddResponse；通过才新增/更新 active，失败只在 failed 返回 |
| `POST /api/proxies/import/scdn` | ScdnProxyImportRequest | 201 ProxyAddResponse；拉取、测试后入库，外部异常 `502` |
| `POST /api/proxies/bulk/test` | ProxyBulkTestRequest | ProxyBulkTestResponse；空 ids `400`，全不存在 `404`，部分不存在只测试找到的项 |
| `POST /api/proxies/bulk/delete` | ProxyBulkActionRequest | ProxyBulkDeleteResponse；非空 ids 优先，否则按 status；两者皆空 `400` |
| `POST /api/proxies/{proxy_id}/test` | 可省略请求体，或 ProxyTestRequest | ProxyTestResult；使用已存地址，忽略输入 proxy_url；更新 active/failed |
| `POST /api/proxies/{proxy_id}/enable` | 无 | ProxyResponse；直接标记 active，不测试 |
| `POST /api/proxies/{proxy_id}/disable` | 无 | ProxyResponse；标记 disabled |
| `DELETE /api/proxies/{proxy_id}` | 无 | 204 |

单个代理不存在 `404`。新增全部失败、SCDN 无代理也可能返回 201 和 status=false，依据响应判断业务结果。

测试/添加示例中的地址需换成自己的代理：

```json
{"proxy_url": "http://127.0.0.1:7890", "test_mode": "chaoxing", "timeout_seconds": 8.0}
```

按状态删除示例：

```json
{"status": "failed"}
```

## 更新与重启

全部要求管理员且无请求体。检查更新也会执行 git fetch，使用这些接口前先处理运行任务。

| 接口 | 响应模型 | 行为 |
| --- | --- | --- |
| `GET /api/update/status` | UpdateStatus | 检查 checkout、origin、分支与工作区 |
| `POST /api/update/apply` | UpdateApplyResult | 工作区干净且有更新时 git pull --ff-only origin 当前分支，再 uv sync |
| `POST /api/update/restart` | RestartResult | 返回后约 1 秒，以当前 Python 和参数重启进程 |

| 响应模型 | 字段 | 说明 |
| --- | --- | --- |
| UpdateStatus | supported: boolean | checkout 是否支持更新 |
| UpdateStatus | branch, current_commit, remote_commit, current_message, remote_message: string/null | 分支与提交 |
| UpdateStatus | has_update, dirty: boolean | 有更新/有本地修改 |
| UpdateStatus | dirty_files, recent_commits: string[] | 修改文件、远程提交摘要 |
| UpdateStatus | ahead, behind: integer | 提交差异 |
| UpdateStatus | error: string/null | 检查错误 |
| UpdateApplyResult | status: boolean, msg: string, stdout: string, stderr: string, need_restart: boolean | 操作结果与命令输出 |
| RestartResult | status: boolean, msg: string | 例如 `{"status":true,"msg":"服务正在重启，请稍等几秒后刷新页面"}` |

已最新版返回成功且 need_restart=false；不支持、有本地修改或拉取失败返回 status=false。代码已拉取但 uv sync 失败同样返回 false，need_restart=true，说明部分更新。命令输出仅用于诊断。

## 后端扩展约定

### 新增 HTTP 接口

1. 在 webapp/schemas 定义输入/输出模型，明确类型、默认值和范围。
2. 在 webapp/routers 建立路由，使用 get_db_session，并依赖 require_login 或 require_admin。
3. 普通用户资源接口还需检查账号 user_id；任务通过关联账号校验，只有登录检查不构成完整隔离。
4. 同步网络放入 fastapi.concurrency.run_in_threadpool，避免阻塞主事件循环。
5. 在 webapp/server.py 的 create_app 注册，更新本文索引和示例。

新增数据库字段需更新 ORM 和迁移。init_db 使用 create_all 与轻量列迁移，没有 Alembic；create_all 不会更新已有表结构。

### 内部 Python 接口

| 入口 | 输入/上下文 | 返回/作用 |
| --- | --- | --- |
| ChaoxingService.verify_login(account) | ORM ChaoxingAccount | 登录结果 dict；隔离会话并保存 Cookie |
| ChaoxingService.fetch_courses(account, force_refresh=False) | ORM 账号、是否绕过缓存 | 标准化课程数组 |
| ChaoxingService.invalidate_cache(account_id) | 账号 ID | 清除缓存 |
| TaskRunner.submit(task_id) | 已入库 ID、调度器已启动 | 提交任务，未启动抛 RuntimeError |
| TaskRunner.cancel(task_id) | 任务 ID | bool，是否找到信号并发送 |
| TaskRunner.cancel_or_finalize_missing(task_id) | 任务 ID | (bool, message)，处理数据库残留 |
| TaskRunner.is_running(task_id) | 任务 ID | bool，已提交待执行任务也可能为 true |
| process_chapter(chaoxing, course, point, speed) | 业务实例、底层数据 | ChapterResult: SUCCESS/ERROR/NOT_OPEN/PENDING；当前不产生 PENDING |
| quick_process_chapter(...) | 同上 | quick 章节处理，返回相同枚举 |
| process_job(chaoxing, course, job, job_info, speed) | 任务点及公共元数据 | StudyResult: SUCCESS/ERROR/FORBIDDEN |
| SessionContext.scope(holder, cookies_provider=None, progress_callback=None, cancel_event=None) | 会话、回调、取消 Event | 上下文管理器，退出恢复 contextvars |
| SessionContext.report_progress(event_type, **payload) | 类型、事件字段 | 调用作用域进度回调 |
| SessionContext.is_cancelled() | 当前上下文 | bool |
| ProgressBus.publish(task_id, event) | ID、事件 dict | 任意线程发布到主循环订阅队列 |
| ProgressBus.subscribe(task_id) / unsubscribe(task_id, queue) | 异步调用 | 创建/移除订阅 |

服务类位于 webapp/services，SessionContext 在 api/session_context.py。底层类为 api.base.Chaoxing，主要方法是 login(login_with_cookies=False)、get_course_list()、get_course_point(courseid, clazzid, cpi)、get_job_list(course, point) 和 study_*。

get_course_point 返回含 points 的 dict，get_job_list 返回 (jobs, job_info)。流程消费底层解析结果，不要用 HTTP 课程响应代替全部底层元数据。SessionManager.get_session 优先使用作用域隔离会话，未绑定时回退进程内会话。

### 新增任务点类型

在 api/decode.py 解析，在 study_flow.process_job 分派，为 quick 决定是否复用普通处理器。返回 StudyResult，不能用 bool 代替。新增任务类型必须在当前任务线程内串行处理，沿用 SessionContext，不得通过新线程或线程池恢复并发。

长循环检查 SessionContext.is_cancelled，必要时抛 CancelledError，用 report_progress 发事件；同步更新前端和 SSE 协议。日志持久化需经过执行器，普通 Loguru 输出不自动入表。

### 新增题库 provider

在 api/answer.py 继承 Tiku，实现 _init_tiku 和 _query(q_info)。后者返回答案字符串或 None，题目数据包含 title/type/options；基类 query 负责标题预处理、缓存和类型检查。

初始化顺序为 config_set(config) → get_tiku_from_config() → init_tiku()。当前通过 api.answer 全局类名查找，因此仅在其他模块定义类不会自动发现。Web 内部配置使用字符串值；新增字段需更新 TikuConfig、设置页和本文。

### 本地联调

从项目目录启动单进程服务：

```shell
uv run python start.py --host 127.0.0.1 --reload --debug
```

先验证 healthz 和 openapi.json，再通过同源 docs 或保持 Cookie 的客户端调用。Swagger 没有独立 token 授权按钮，需先调用登录接口建立会话。「Try it out」会执行真实请求，任务、代理测试、更新和重启都有副作用。

回归测试使用标准库 unittest，在项目根执行 `uv run python -m unittest discover -s tests -t . -v`。tests/support.py 在导入应用前将配置、数据库和日志隔离到测试上下文；外部登录、课程查询和代理连通性使用 mock。页面事件用 Node.js 执行，无 Node.js 时跳过该项。

`node tests/ui_actions.cjs` 单独验证管理操作失败反馈和实际请求参数，无 npm 依赖。2026-10-09 验收为 67 项 Python 测试通过和 19 条前端管理失败路径通过，详见[按钮功能验收表](ui-button-acceptance.md)。这些检查不替代外部平台实测，也不调用真实在线更新或重启接口。

## 当前行为与限制

| 代码位置 | 当前行为 | 接入建议 |
| --- | --- | --- |
| task_runner.py、study_flow.py、quick_flow.py | 全局账号调度和章节任务点均串行，jobs 只允许 1 | 其他账号的任务等待当前任务结束；旧并发设置无效 |
| task_runner.py | 未开放章节未落实 retry/ask | 用日志提示，不承诺重试或交互 |
| db.py、task_runner.py | 历史 done 无法还原结果分类，新任务增加分类计数和 partial | 区分已处理、成功、失败、跳过与历史未分类 |
| task_runner.py | 重启将遗留任务标记 cancelled | 之后需重新创建任务 |
| progress_bus.py | 单进程内存，可丢旧消息 | 单 worker，详情/日志兜底 |
| settings.py、task_runner.py | 通知仅保存，failover 不切换 | 不将保存解释为执行成功 |
| deps.py | 用户启停仅在登录时检查 | 禁用/删除不会立即撤销 Session |
| server.py | 部分 ImportError 被忽略，healthz 仍成功 | 核对 OpenAPI 路由和启动日志 |

返回 [README](../README.md)。
