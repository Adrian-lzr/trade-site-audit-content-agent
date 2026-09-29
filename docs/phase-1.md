# 第 1 阶段：运行与验收状态

本文按当前仓库实现记录可用操作、已完成的针对性验证及尚未验收的内容（最后核对：2026-09-29）。进程拆分为 API、Worker、可选 Fixture 和 Web；本地任务消费要求 API 与 Worker 连接同一个数据库。

## 当前能力

- `apps/web` 是 React/Vite 工作台，默认端口 `5173`，将 `/api` 代理到 `127.0.0.1:8000`。
- FastAPI 入口为 `uvicorn backend.app:app --reload --port 8000`；启动时由 `init_db()` 应用 Alembic 迁移并确保 `demo-workspace` 存在。
- 持久任务由独立命令 `python -m backend.worker` 消费。先等待 API 完成迁移并通过 health 检查，再启动 Worker；API 不在 lifespan 中启动 Worker，Worker 也不执行数据库迁移。
- `backend/models.py` 定义 Workspace、Site、Page、PageSnapshot、Job、审计结果、版本化 Fact、变更审批、版本化采购问题集及内容生成任务数据；首次迁移为 `backend/migrations/versions/0001_initial.py`，当前迁移 head 为 `0008_content_generation_tasks`。
- Fixture 命令为 `python -m backend.fixture_server --port 8765`，只监听本机 loopback；注册 loopback 站点需要在 API 与 Worker 进程显式设置 `ALLOW_LOOPBACK=true`。
- 默认数据库为 SQLite。根 `docker-compose.yml` 只启动 PostgreSQL，不启动 API、Worker 或 Web。
- 根 `Makefile` 提供 `api`、`worker`、`fixture`、`web`、`db-migrate`、`test`、`web-build` 和 `health` 入口；长运行目标仍需分别在终端启动。
- 健康检查同时提供直接端点 `/health` 和兼容端点 `/api/health`；页面摘要接口返回 `rule_count`、`rule_problem_count`、`rule_review_count`、`rule_unknown_count`，分别表示规则总数、失败、需人工复核和未知结果数量。

## 阶段验收

| 项目 | 已有证据 | 尚未验收 |
| --- | --- | --- |
| Python 测试 | Python 3.12 venv 使用 `backend/requirements.lock`；最近完整运行 `backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider backend/tests -q` 为 57 passed、82 warnings，覆盖审计、事实可见性、审批版本冲突、发布前置条件、采购问题集和内容生成 | 本轮 82 条警告均为 Alembic `path_separator` 弃用提示；测试通过不代表 PostgreSQL、自然租约过期或并发压力已验收 |
| SQLite 迁移 | 空库可从 `0001` 升到 `0008_content_generation_tasks`；旧事实由迁移保守回填为 `internal_only`，另含租约、合成来源标记、版本化规则、事实、变更审批、outbox、版本化采购问题集、页面映射和内容生成任务表 | PostgreSQL 迁移未运行 |
| 本地演示 | 独立 API、Worker、Fixture、Web 进程端到端通过；任务 succeeded，页面快照带 hash 与 `is_synthetic=true`，并产生 `TITLE_MISSING` | 当前验证为本机隔离 SQLite 演示，不代表生产部署验收 |
| 真实 HTTPS 只读检测 | 在已获站点访问授权、限定采集范围和遵守访问频率的前提下，2026-09-28 对 `https://example.com` 完成一次只读检测：job `succeeded`、HTTP `200`、内容 hash `ff67a9d764d6a2367a187734e697f6a53217db9a21c101d410a113ca871a299d`，12 条规则均执行，其中 1 条为 `needs_review` | 这是一次授权范围内的可复现采样，不代表搜索引擎收录、排名、AI 引用或业务增长；真实客户站点需重新确认授权和范围 |
| API 兼容入口与页面摘要 | `/health` 直接端点和 `/api/health` 均返回 `status: ok`；页面列表摘要包含规则总数、失败、需复核和未知计数 | 未在 PostgreSQL 浏览器演示中复核 |
| Worker 租约 | 单元测试覆盖未过期租约保留、过期任务重排、旧 token 无法提交快照；执行审计时会在 robots、sitemap 和页面请求前阶段续租；进程级测试在抓取中强制终止 Worker、将租约标记为过期，再启动独立 Worker 恢复并确认只产生一份快照。页面 upsert 回归测试仅在 SQLite 上验证 | 未等待真实 5 分钟租约自然过期；续租发生在阶段请求之间，单次超长网络请求仍可能超过租约。PostgreSQL upsert 分支及并发压力仍未运行验证 |
| SSRF 边界 | scheme/host/port 同源校验、编码 traversal、重定向拒绝、已解析 IP 固定连接测试通过 | DNS rebinding 与完整 SSRF 安全验证未完成；transport 使用受锁文件约束的 httpcore 网络后端接口 |
| Web 构建与浏览器 | `npm run build` 通过；Playwright 检查 1440px 与 390px，页面无视口横向溢出；事实导入默认“仅内部”，移动弹窗可纵向滚动到提交按钮 | 快照表格在窄屏内独立横向滚动；PostgreSQL 未参与浏览器演示 |
| 运行入口与基础设施 | 根 `Makefile` 已提供 API、Worker、Fixture、Web、迁移、测试、构建和 health 命令；`.env.example` 列出 SQLite/PostgreSQL 配置边界；README 包含 PostgreSQL/SQLite 备份恢复步骤 | Make 本身未在当前环境执行；不同 shell 的命令解析需在目标开发机复核 |
| PostgreSQL | `docker-compose.yml` 含 PostgreSQL 16、持久卷和 healthcheck | Docker/Compose 与 PostgreSQL 未在当前环境验证（当前环境没有 Docker CLI） |

上表是阶段记录，不代表尚未复测的内容已经通过。遇到过期记录时，应以相应代码当前状态和最近一次验证结果为准。

本轮只读复核（2026-09-28）：API `8001` 的 `/health` 返回 `status: ok`；工作台 `5174` 的 API 代理请求成功。已登记的 `https://example.com` 有 2 个既有审计 job，均为 `succeeded`，各采集 1 页、HTTP `200`，执行 12 条规则并各有 1 条 `needs_review`；两份快照内容 hash 相同。本轮未发起新的公网采集请求，也未调用发布接口。演示工作区的事实 API 返回 1 条已确认事实，其 `visibility` 为 `internal_only`。

本轮后端完整测试为 57 passed、82 warnings；82 条警告均为 Alembic `path_separator` 弃用提示。该测试使用 `backend/tests/conftest.py` 创建的临时 SQLite 库，网络测试只使用本机 fixture 或 `MockTransport`。`npm --prefix apps/web run build`、Playwright 桌面/移动检查以及 Compose 记录仍是 2026-09-28 的历史结果，本轮未重跑。Compose 配置未执行（当前环境没有 Docker CLI）。

## 本地启动

从仓库根目录创建虚拟环境并安装 Python 依赖（推荐使用精确锁文件）：

```powershell
uv venv --python 3.12 .venv
.venv\Scripts\Activate.ps1
uv pip install --python .venv\Scripts\python.exe -r backend\requirements.lock
```

终端 A 启动 API，终端 B 启动 Worker；两者应使用相同 `DATABASE_URL`：

```powershell
uvicorn backend.app:app --reload --port 8000
```

```powershell
python -m backend.worker
```

默认 SQLite 文件为仓库目录下的 `backend.db`，API startup 会运行迁移。独立执行迁移可用 `python -m alembic -c backend/alembic.ini upgrade head`；根目录也提供 `make db-migrate`。

API 启动后执行 `Invoke-RestMethod http://127.0.0.1:8000/health`（或 `make health`），确认 `status: ok` 后再启动 Worker。API 和 Worker 必须使用同一个 `DATABASE_URL`，否则任务会停留在 API 所连接数据库的队列中。

在 `apps/web` 目录启动工作台：

```powershell
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173`。Web `/api` 代理到 `127.0.0.1:8000`。

停止 API 和 Worker 后，SQLite 演示可直接复制 `backend.db` 备份；恢复时替换文件并重新启动 API。PostgreSQL 的 `pg_dump`/`psql` 命令见根目录 README。备份可能包含 HTML 和审计证据，不要提交到仓库。

## Fixture 流程

终端 A 启动本机静态 Fixture（该进程不读取 `ALLOW_LOOPBACK`）：

```powershell
python -m backend.fixture_server --port 8765
```

终端 B 在开启 loopback 的环境启动 API：

```powershell
$env:ALLOW_LOOPBACK = "true"
uvicorn backend.app:app --reload --port 8000
```

终端 C 使用同一数据库并开启 loopback 启动 Worker：

```powershell
$env:ALLOW_LOOPBACK = "true"
python -m backend.worker
```

创建站点、审计并检查结果：

```powershell
$site = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/sites -ContentType 'application/json' -Body (@{ workspace_id = 'demo-workspace'; name = 'local fixture'; origin = 'http://127.0.0.1:8765'; allowed_paths = @('/'); is_synthetic = $true } | ConvertTo-Json)
$run = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/sites/$($site.id)/audit-runs" -ContentType 'application/json' -Body '{}'
do { Start-Sleep -Seconds 1; $run = Invoke-RestMethod "http://127.0.0.1:8000/api/audit-runs/$($run.id)" } while ($run.status -in @('queued', 'running'))
Invoke-RestMethod "http://127.0.0.1:8000/api/sites/$($site.id)/pages"
```

Fixture 首页是合成样本，刻意没有 `<title>`；演示预期为 `title: null` 并产生一条 `TITLE_MISSING` finding。浏览器工作台也可以执行相同的站点登记和审计操作。

## 真实 HTTPS 只读检测

在获得站点所有者或运营方明确授权、确认允许采集的路径和访问频率后，可以将公开 HTTPS 站点作为只读审计目标。审计只读取 HTML、robots.txt、sitemap 和有界站内链接，不执行发布、写入或内容修改。

2026-09-28 的授权演示对 `https://example.com` 成功完成一个 job：状态为 `succeeded`，目标页面 HTTP 状态为 `200`，页面内容 hash 为 `ff67a9d764d6a2367a187734e697f6a53217db9a21c101d410a113ca871a299d`；冻结的规则结果共 12 条，其中 1 条状态为 `needs_review`。该记录证明一次只读采样链路可运行，不证明搜索引擎收录、排名、AI 引用或询盘增长。

## 针对性检查

```powershell
python -m pytest backend/tests -q
npm --prefix apps/web run build
```

越界 URL 与 IP 固定连接的针对性测试位于 `backend/tests/test_crawler.py`。该测试范围不等价于完整 SSRF 安全保证。`backend/tests/test_worker_process_recovery.py` 会在抓取中终止独立 Worker 进程，并通过推进租约到期时间加速恢复验证；真实 5 分钟自然过期和 PostgreSQL 环境仍未验证。Worker 在阶段请求之间续租；若单次请求本身超过租约，旧 Worker 的提交仍会因 token 失效而被拒绝，任务随后可由新 Worker 恢复。

## 第 1 阶段边界

本阶段交付站点登记、受限 HTTP 采集、持久化审计任务、HTML 快照、确定性规则证据和本地工作台。事实库已在第 3 阶段以独立纵向完成，详见 [第 3 阶段状态](phase-3.md)。第 4 阶段现有变更草稿、版本化审批与事务 outbox 的后端骨架，但 Git 发布器、outbox 消费 Worker、上线复查、回滚和可见性监测仍未验收，详见[第 4 阶段状态](phase-4.md)。真实站点继续保持只读，不把 `not_configured` 入口当成已发布能力。
