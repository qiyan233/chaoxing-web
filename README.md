# 超星学习通自动化平台 Web 版

这是从原项目拆分出的纯 Web 控制台版本，保留超星接口、题库、课程任务处理等核心能力，移除了命令行入口和 `config.ini`/`cookies.txt` 工作流。运行后通过浏览器管理账号、选择课程、启动任务并查看实时日志。

## 功能

- 多账号管理：账号密码加密保存到本地 SQLite。
- 课程选择：登录后拉取课程列表，可按课程创建任务。
- 后台执行：APScheduler 负责提交任务，每个账号使用独立 HTTP Session。
- 实时进度：任务日志和章节进度通过 SSE 推送到任务详情页。
- 题库配置：在 Web 设置页配置题库 provider、token、提交策略等。
- 任务取消：运行中的任务可在 Web 页面中取消。

## Linux 启动（uv）

```bash
cd chaoxing-web
uv sync
uv run python start.py
```

默认访问地址：

```text
http://localhost:3000
```

## Windows 启动（uv）

```powershell
cd C:\Users\qi\Desktop\ai\chaoxing\chaoxing-web
uv sync
uv run python start.py
```

如果需要指定端口或开发热重载：

```powershell
uv run python start.py --port 3000 --reload
```

首次访问会跳转到 `/setup` 设置管理员密码。运行数据保存在 `data/`：

- `data/chaoxing.db`：账号、任务、日志、设置。
- `data/.secret_key`：超星密码 Fernet 加密密钥。
- `data/.session_secret`：浏览器 Session 签名密钥。

这些文件属于本地敏感运行数据，请勿提交或泄漏。

## Docker

```bash
docker build -t chaoxing-web .
docker run -p 3000:3000 -v ./data:/app/data chaoxing-web
```

## 环境变量

- `CHAOXING_HOST`：监听地址，默认 `0.0.0.0`。
- `CHAOXING_PORT`：监听端口，默认 `3000`。
- `CHAOXING_DEBUG`：设置为 `true` 开启调试日志。
- `CHAOXING_MAX_ACCOUNTS`：后台同时运行的账号数，默认 `1`。
- `CHAOXING_COURSE_CACHE`：课程列表缓存秒数，默认 `300`。

## 项目结构

```text
api/                 超星接口、题库、页面解析、任务点处理底层能力
resource/            字体映射等资源文件
webapp/              FastAPI Web 应用
webapp/routers/      页面与 API 路由
webapp/services/     账号服务、任务执行器、进度推送、凭据加密
webapp/templates/    Jinja2 页面模板
webapp/static/       前端静态资源
```

## 说明

Web 版不再提供 `main.py`、`config_template.ini`、`cookies.txt` 模式。所有配置都应通过浏览器页面或环境变量完成。
